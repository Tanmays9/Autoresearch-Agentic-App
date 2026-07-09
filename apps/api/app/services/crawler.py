from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    CrawlJob,
    CrawlTarget,
    Project,
    ResearchRun,
    ResearchSettings,
    Source,
    SourceChunk,
    SourceSnapshot,
    utcnow,
)
from .embeddings import cosine_similarity, embed_text
from .evidence import FetchedDocument, fetch_document, validate_public_url
from .events import add_event


USER_AGENT = "AtlasResearch/0.1 (+local cited research tool)"
_domain_semaphores: dict[str, asyncio.Semaphore] = {}
_domain_gates: dict[str, asyncio.Lock] = {}
_domain_last_request: defaultdict[str, float] = defaultdict(float)
_robots_cache: dict[str, tuple[float, RobotFileParser | None]] = {}


def get_research_settings(db: Session) -> ResearchSettings:
    settings = db.get(ResearchSettings, 1)
    if not settings:
        settings = ResearchSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if not key.casefold().startswith("utm_")],
        doseq=True,
    )
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunparse((parsed.scheme.casefold(), parsed.netloc.casefold(), path, "", query, ""))


def ensure_crawl_job(db: Session, run: ResearchRun, max_pages: int | None = None) -> CrawlJob:
    job = db.scalar(select(CrawlJob).where(CrawlJob.run_id == run.id))
    if job:
        return job
    configured = get_research_settings(db)
    job = CrawlJob(
        project_id=run.project_id,
        run_id=run.id,
        max_pages=min(200, max_pages if max_pages is not None else (run.source_budget or configured.default_source_budget)),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    add_event(db, run.id, "crawl_queued", f"Topic crawler queued with a {job.max_pages}-page limit.")
    db.commit()
    return job


def add_crawl_targets(
    db: Session,
    job: CrawlJob,
    candidates: list[dict],
    *,
    depth: int = 0,
    parent_url: str | None = None,
) -> int:
    configured = get_research_settings(db)
    added = 0
    for candidate in candidates:
        if job.discovered_count >= job.max_pages:
            break
        raw_url = str(candidate.get("url") or "").strip()
        try:
            url = canonicalize_url(raw_url)
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
        except Exception:
            continue
        domain = parsed.hostname.casefold().removeprefix("www.")
        domain_count = db.scalar(
            select(func.count()).select_from(CrawlTarget).where(CrawlTarget.job_id == job.id, CrawlTarget.domain == domain)
        ) or 0
        if domain_count >= get_settings().crawl_domain_page_limit:
            continue
        existing = db.scalar(select(CrawlTarget).where(CrawlTarget.job_id == job.id, CrawlTarget.url == url))
        if existing:
            continue
        target = CrawlTarget(
            job_id=job.id,
            url=url,
            title=candidate.get("title"),
            query=candidate.get("query"),
            relevance_reason=candidate.get("relevance_reason") or candidate.get("description"),
            depth=min(depth, get_settings().crawl_max_depth),
            parent_url=parent_url,
            domain=domain,
        )
        db.add(target)
        job.discovered_count += 1
        added += 1
    if added:
        db.commit()
    return added


def register_agent_candidates(db: Session, run: ResearchRun, candidates: list) -> int:
    if not candidates:
        return 0
    job = ensure_crawl_job(db, run)
    values = [item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item) for item in candidates]
    added = add_crawl_targets(db, job, values)
    if added:
        if job.status in {"completed", "cancelled"}:
            job.status = "queued"
            job.completed_at = None
            job.error = None
        add_event(db, run.id, "agent_sources_discovered", f"A research agent added {added} source candidates to the protected crawler.")
        db.commit()
    return added


async def _robots_allowed(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    cached = _robots_cache.get(robots_url)
    if cached and time.monotonic() - cached[0] < 3600:
        return True if cached[1] is None else cached[1].can_fetch(USER_AGENT, url)
    try:
        await validate_public_url(robots_url)
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(robots_url)
        if response.status_code >= 400:
            _robots_cache[robots_url] = (time.monotonic(), None)
            return True
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        _robots_cache[robots_url] = (time.monotonic(), parser)
        return parser.can_fetch(USER_AGENT, url)
    except Exception:
        _robots_cache[robots_url] = (time.monotonic(), None)
        return True


async def _polite_fetch(url: str, domain: str, per_domain: int) -> FetchedDocument:
    semaphore = _domain_semaphores.setdefault(domain, asyncio.Semaphore(per_domain))
    gate = _domain_gates.setdefault(domain, asyncio.Lock())
    async with semaphore:
        async with gate:
            delay = 1.0 - (time.monotonic() - _domain_last_request[domain])
            if delay > 0:
                await asyncio.sleep(delay)
            _domain_last_request[domain] = time.monotonic()
        return await fetch_document(url)


def _lexical_relevance(topic: str, title: str | None, content: str) -> float:
    words = {item for item in re.findall(r"[a-z0-9]{3,}", topic.casefold())}
    if not words:
        return 1.0
    haystack = f"{title or ''} {content[:5000]}".casefold()
    return sum(1 for word in words if word in haystack) / len(words)


async def _relevance(topic: str, document: FetchedDocument) -> float:
    lexical = _lexical_relevance(topic, document.title, document.content)
    try:
        left, right = await asyncio.gather(
            asyncio.to_thread(embed_text, topic),
            asyncio.to_thread(embed_text, f"{document.title or ''}\n{document.content[:5000]}"),
        )
        semantic = cosine_similarity(left, right) if left and right else lexical
        return round(max(0.0, min(1.0, semantic * 0.7 + lexical * 0.3)), 4)
    except Exception:
        return round(lexical, 4)


async def _crawl_target(topic: str, target: CrawlTarget, per_domain: int) -> tuple[str, object]:
    if not await _robots_allowed(target.url):
        return "skipped", "Blocked by robots.txt"
    try:
        document = await _polite_fetch(target.url, target.domain, per_domain)
        score = await _relevance(topic, document)
        if target.depth > 0 and score < 0.12:
            return "irrelevant", (document, score)
        return "fetched", (document, score)
    except Exception as exc:
        return "failed", str(exc)[:1000]


def _chunk_text(content: str, size: int = 4000, overlap: int = 250) -> list[str]:
    chunks: list[str] = []
    position = 0
    while position < len(content):
        chunk = content[position : position + size].strip()
        if chunk:
            chunks.append(chunk)
        position += max(1, size - overlap)
    return chunks[:250]


async def process_crawl_jobs(db: Session) -> int:
    job = db.scalar(select(CrawlJob).where(CrawlJob.status.in_(["queued", "running"])).order_by(CrawlJob.created_at))
    if not job:
        return 0
    now = utcnow()
    if job.status == "queued":
        job.status = "running"
        job.started_at = now
        job.deadline_at = now + timedelta(minutes=get_settings().crawl_deadline_minutes)
        add_event(db, job.run_id, "crawl_started", f"Crawler started with {job.discovered_count} seed URLs.")
        db.commit()
    deadline_now = now if not job.deadline_at or job.deadline_at.tzinfo else now.replace(tzinfo=job.deadline_at.tzinfo)
    if job.deadline_at and job.deadline_at < deadline_now:
        job.status = "completed"
        job.completed_at = now
        job.error = "Crawler stopped at the 30-minute deadline."
        add_event(db, job.run_id, "crawl_deadline", job.error)
        db.commit()
        return 1
    # There is only one local crawler worker. A target can remain in
    # ``fetching`` only when that worker was interrupted after leasing a batch.
    # Return such targets to the queue so container restarts are recoverable.
    abandoned = db.scalars(
        select(CrawlTarget).where(CrawlTarget.job_id == job.id, CrawlTarget.status == "fetching")
    ).all()
    for target in abandoned:
        target.status = "queued"
        target.error = "Recovered after crawler worker restart."
    if abandoned:
        db.commit()
    configured = get_research_settings(db)
    remaining = max(0, job.max_pages - job.processed_count)
    targets = db.scalars(
        select(CrawlTarget)
        .where(CrawlTarget.job_id == job.id, CrawlTarget.status == "queued")
        .order_by(CrawlTarget.depth, CrawlTarget.discovered_at)
        .limit(min(configured.crawler_concurrency, remaining))
    ).all()
    if not targets:
        active = db.scalar(
            select(func.count()).select_from(CrawlTarget).where(
                CrawlTarget.job_id == job.id, CrawlTarget.status.in_(["queued", "fetching"])
            )
        ) or 0
        if not active or job.processed_count >= job.max_pages:
            job.status = "completed"
            job.completed_at = utcnow()
            add_event(db, job.run_id, "crawl_completed", f"Crawler fetched {job.fetched_count} of {job.processed_count} processed pages.")
            db.commit()
        return 0
    for target in targets:
        target.status = "fetching"
    db.commit()
    project = db.get(Project, job.project_id)
    results = await asyncio.gather(
        *[_crawl_target(project.topic if project else "", target, configured.crawler_per_domain) for target in targets]
    )
    # Embedding can take seconds per document.  Compute it before mutating or
    # flushing ORM objects so SQLite never holds its single writer lock while
    # CPU work is awaited; LangGraph agents can then persist events in parallel.
    prepared_chunks: dict[int, list[tuple[str, list[float] | None]]] = {}
    for result_index, (status, payload) in enumerate(results):
        if status != "fetched":
            continue
        document, _score = payload
        values: list[tuple[str, list[float] | None]] = []
        for chunk in _chunk_text(document.content):
            vector = await asyncio.to_thread(embed_text, chunk[:4000])
            values.append((chunk, vector))
        prepared_chunks[result_index] = values

    for result_index, (target, (status, payload)) in enumerate(zip(targets, results, strict=True)):
        job.processed_count += 1
        target.fetched_at = utcnow()
        if status in {"skipped", "failed"}:
            target.status = status
            target.error = str(payload)
            if status == "failed":
                job.failed_count += 1
            else:
                job.skipped_count += 1
            continue
        document, score = payload
        target.relevance_score = score
        if status == "irrelevant":
            target.status = "skipped"
            target.error = "Page did not meet the topic relevance threshold."
            job.skipped_count += 1
            continue
        duplicate = db.scalar(
            select(Source).where(Source.project_id == job.project_id, Source.content_hash == document.content_hash)
        )
        if duplicate:
            target.status = "skipped"
            target.source_id = duplicate.id
            target.error = "Duplicate content"
            job.skipped_count += 1
            continue
        source = db.scalar(select(Source).where(Source.project_id == job.project_id, Source.url == target.url))
        if not source:
            source = Source(project_id=job.project_id, run_id=job.run_id, url=target.url)
            db.add(source)
            db.flush()
        source.title = document.title or target.title or source.title
        source.status = "fetched"
        source.content_hash = document.content_hash
        snapshot = SourceSnapshot(
            source_id=source.id,
            content=document.content,
            content_type=document.content_type,
            http_status=document.status_code,
        )
        db.add(snapshot)
        db.flush()
        for index, (chunk, vector) in enumerate(prepared_chunks.get(result_index, [])):
            db.add(
                SourceChunk(
                    source_id=source.id,
                    snapshot_id=snapshot.id,
                    chunk_index=index,
                    content=chunk,
                    embedding_json=json.dumps(vector) if vector else None,
                )
            )
        target.source_id = source.id
        target.canonical_url = canonicalize_url(document.canonical_url or document.url)
        target.status = "fetched"
        job.fetched_count += 1
        if target.depth < get_settings().crawl_max_depth and job.discovered_count < job.max_pages:
            add_crawl_targets(
                db,
                job,
                [{"url": link, "relevance_reason": f"Linked from {target.url}"} for link in document.links],
                depth=target.depth + 1,
                parent_url=target.url,
            )
    db.commit()
    return len(targets)
