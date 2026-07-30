from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import CrawlTarget, Project, Source, SourceChunk
from app.services import crawler
from app.services.crawler import add_crawl_targets, canonicalize_url, ensure_crawl_job, process_crawl_jobs
from app.services.evidence import FetchedDocument
from app.services.orchestration import create_run


def test_url_canonicalization_removes_tracking_and_fragments():
    value = canonicalize_url("HTTPS://Example.COM/a//b?utm_source=x&q=1#section")
    assert value == "https://example.com/a/b?q=1"


async def test_crawler_respects_page_limit_and_stores_chunks(monkeypatch):
    async def fake_crawl(_topic, target, _per_domain):
        return "fetched", (
            FetchedDocument(
                target.url,
                f"Useful research content for {target.url} " * 100,
                "text/html",
                200,
                title=f"Source {target.domain}",
            ),
            0.91,
        )

    monkeypatch.setattr(crawler, "_crawl_target", fake_crawl)
    with SessionLocal() as db:
        project = Project(title="Crawler", topic="knowledge graph retrieval")
        db.add(project)
        db.commit()
        run = create_run(db, project)
        job = ensure_crawl_job(db, run)
        job.max_pages = 2
        db.commit()
        added = add_crawl_targets(
            db,
            job,
            [
                {"url": "https://one.example/article"},
                {"url": "https://two.example/article"},
                {"url": "https://three.example/article"},
            ],
        )
        assert added == 2
        assert await process_crawl_jobs(db) == 2
        assert db.scalar(select(func.count()).select_from(Source)) == 2
        assert db.scalar(select(func.count()).select_from(SourceChunk)) >= 2
        assert set(db.scalars(select(CrawlTarget.status)).all()) == {"fetched"}
