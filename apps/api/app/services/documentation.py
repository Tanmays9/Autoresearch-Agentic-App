from __future__ import annotations

import io
import json
import re
import zipfile
from collections import Counter

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    ApprovalDecision,
    Claim,
    CoursePage,
    CoursePageClaim,
    CoursePageSource,
    CoursePageVersion,
    CourseRelease,
    CourseVersion,
    DocumentationExperiment,
    DocumentationRun,
    Evidence,
    Project,
    Source,
    utcnow,
    uuid4str,
)
from .embeddings import cosine_similarity, embed_text


PAGE_FAMILIES = [
    ("overview", "Overview", "overview"),
    ("foundations", "Foundations", "foundations"),
    ("core-concepts", "Core Concepts", "core_concept"),
    ("tutorials", "Tutorials", "tutorial"),
    ("how-to-guides", "How-to Guides", "how_to"),
    ("evaluation", "Evaluation", "evaluation"),
    ("troubleshooting", "Troubleshooting", "troubleshooting"),
    ("reference", "Reference", "reference"),
    ("glossary", "Glossary", "glossary"),
    ("sources", "Sources", "sources"),
    ("unresolved-questions", "Unresolved Questions", "unresolved"),
]
PAGE_PARENTS = {
    "glossary": "reference",
    "sources": "reference",
    "unresolved-questions": "reference",
}


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return value[:120] or "page"


def extract_headings(markdown: str) -> list[dict]:
    headings = []
    for match in re.finditer(r"^(#{2,4})\s+(.+?)\s*$", markdown, flags=re.MULTILINE):
        headings.append({"level": len(match.group(1)), "title": match.group(2), "anchor": slugify(match.group(2))})
    return headings


def documentation_metrics(markdown: str, source_count: int = 0) -> dict[str, float]:
    words = re.findall(r"\b[\w'-]+\b", markdown)
    sentences = [item for item in re.split(r"[.!?]+", re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)) if item.strip()]
    headings = re.findall(r"^#{2,4}\s+", markdown, flags=re.MULTILINE)
    citations = len(re.findall(r"\[\^\d+\]|https?://", markdown)) + source_count
    evidence = min(35.0, citations * 5.0)
    curriculum = min(25.0, len(words) / 40.0)
    structure = min(15.0, len(headings) * 3.0 + (3.0 if re.search(r"^[-*]\s+", markdown, re.MULTILINE) else 0.0))
    average_sentence = len(words) / max(1, len(sentences))
    readability = max(0.0, 15.0 - abs(18.0 - average_sentence) * 0.45)
    normalized = [word.casefold() for word in words if len(word) > 4]
    repeats = sum(count - 1 for count in Counter(normalized).values() if count > 3)
    duplication = max(0.0, 10.0 - repeats / max(1, len(normalized)) * 30.0)
    return {
        "evidence_coverage": round(evidence, 2),
        "curriculum_coverage": round(curriculum, 2),
        "structure_navigation": round(structure, 2),
        "readability": round(readability, 2),
        "low_duplication": round(duplication, 2),
        "total": round(evidence + curriculum + structure + readability + duplication, 2),
    }


def _split_markdown(markdown: str) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {slug: [] for slug, _, _ in PAGE_FAMILIES}
    parts = re.split(r"(?=^##\s+)", markdown, flags=re.MULTILINE)
    preamble = parts.pop(0).strip() if parts else markdown.strip()
    if preamble:
        buckets["overview"].append(preamble)
    for part in parts:
        title_match = re.match(r"^##\s+(.+)$", part, flags=re.MULTILINE)
        title = title_match.group(1).casefold() if title_match else ""
        if "source" in title or "bibliograph" in title:
            key = "sources"
        elif "unresolved" in title or "question" in title:
            key = "unresolved-questions"
        elif any(word in title for word in ("evaluation", "benchmark", "metric")):
            key = "evaluation"
        elif any(word in title for word in ("troubleshoot", "failure", "limitation", "risk")):
            key = "troubleshooting"
        elif any(word in title for word in ("tutorial", "walkthrough")):
            key = "tutorials"
        elif any(word in title for word in ("how-to", "implementation", "practical")):
            key = "how-to-guides"
        elif any(word in title for word in ("foundation", "prerequisite", "learning path")):
            key = "foundations"
        elif any(word in title for word in ("glossary", "terminology")):
            key = "glossary"
        elif any(word in title for word in ("reference", "api")):
            key = "reference"
        else:
            key = "core-concepts"
        buckets[key].append(part.strip())
    return buckets


def _stable_page(db: Session, project_id: str, stable_key: str) -> CoursePage:
    page = db.scalar(select(CoursePage).where(CoursePage.project_id == project_id, CoursePage.stable_key == stable_key))
    if page:
        return page
    page = CoursePage(project_id=project_id, stable_key=stable_key)
    db.add(page)
    db.flush()
    return page


def materialize_course_release(
    db: Session,
    course: CourseVersion,
    *,
    publish: bool = False,
    status: str | None = None,
) -> CourseRelease:
    existing = db.scalar(select(CourseRelease).where(CourseRelease.legacy_course_version_id == course.id))
    if existing:
        return existing
    project = db.get(Project, course.project_id)
    version = (db.scalar(select(func.max(CourseRelease.version)).where(CourseRelease.project_id == course.project_id)) or 0) + 1
    release_status = status or ("published" if publish else "legacy")
    release = CourseRelease(
        project_id=course.project_id,
        run_id=course.run_id,
        legacy_course_version_id=course.id,
        version=version,
        title=project.title if project else "Atlas course",
        summary=f"Documentation release generated from research course v{course.version}.",
        status=release_status,
        published_at=utcnow() if release_status == "published" else None,
    )
    db.add(release)
    db.flush()
    buckets = _split_markdown(course.markdown)
    learning_markdown = "\n\n".join(buckets["foundations"])
    learning_parts = re.split(r"(?=^###\s+(?:\d+\.)?\s*)", learning_markdown, flags=re.MULTILINE)
    learning_intro = learning_parts[0].strip() if learning_parts else ""
    child_sections: list[tuple[str, str, str]] = []
    for section in learning_parts[1:]:
        heading = re.match(r"^###\s+(?:\d+\.)?\s*(.+)$", section, flags=re.MULTILINE)
        if not heading:
            continue
        title = heading.group(1).strip()
        child_slug = slugify(title)
        child_sections.append((f"core-concepts/{child_slug}", title, section.strip()))
    if child_sections:
        buckets["foundations"] = [learning_intro] if learning_intro else []
        buckets["core-concepts"] = [
            "# Core Concepts\n\nExplore each evidence-backed research section as a separate course page.\n\n"
            + "\n".join(f"- [{title}](/projects/{course.project_id}/docs/core-concepts/{slug.rsplit('/', 1)[-1]})" for slug, title, _ in child_sections)
        ]
    page_id_by_slug: dict[str, str] = {}
    for position, (slug, title, page_type) in enumerate(PAGE_FAMILIES):
        page = _stable_page(db, course.project_id, slug)
        page_id_by_slug[slug] = page.id
        content = "\n\n".join(buckets[slug]).strip()
        if not content:
            content = f"# {title}\n\n_No verified content is available for this section yet._"
        elif not content.startswith("#"):
            content = f"# {title}\n\n{content}"
        metrics = documentation_metrics(content)
        page_version = CoursePageVersion(
            page_id=page.id,
            release_id=release.id,
            parent_page_id=page_id_by_slug.get(PAGE_PARENTS.get(slug, "")),
            slug=slug,
            title=title,
            page_type=page_type,
            position=position,
            markdown=content,
            summary=re.sub(r"[#>*_`\[\]]", "", content)[:280],
            status=release_status,
            headings_json=json.dumps(extract_headings(content), ensure_ascii=False),
            quality_score=metrics["total"],
        )
        db.add(page_version)
        db.flush()
    core_parent_id = page_id_by_slug.get("core-concepts")
    for offset, (slug, title, content) in enumerate(child_sections, start=len(PAGE_FAMILIES)):
        stable_key = slug
        page = _stable_page(db, course.project_id, stable_key)
        metrics = documentation_metrics(content)
        db.add(
            CoursePageVersion(
                page_id=page.id,
                release_id=release.id,
                parent_page_id=core_parent_id,
                slug=slug,
                title=title,
                page_type="core_concept",
                position=offset,
                markdown=content,
                summary=re.sub(r"[#>*_`\[\]]", "", content)[:280],
                status=release_status,
                headings_json=json.dumps(extract_headings(content), ensure_ascii=False),
                quality_score=metrics["total"],
            )
        )
        db.flush()
    _link_release_evidence(db, release)
    return release


def _link_release_evidence(db: Session, release: CourseRelease) -> None:
    pages = db.scalars(select(CoursePageVersion).where(CoursePageVersion.release_id == release.id)).all()
    claims = db.scalars(
        select(Claim).where(Claim.project_id == release.project_id, Claim.status.in_(["supported", "single_source", "reviewed_supported", "user_accepted"]))
    ).all()
    for page in pages:
        text = page.markdown.casefold()
        linked_claim_ids = {
            item[0]
            for item in db.execute(
                select(CoursePageClaim.claim_id).where(CoursePageClaim.page_version_id == page.id)
            ).all()
        }
        linked_source_ids = {
            item[0]
            for item in db.execute(
                select(CoursePageSource.source_id).where(CoursePageSource.page_version_id == page.id)
            ).all()
        }
        for claim in claims:
            tokens = [token for token in re.findall(r"\b\w{5,}\b", claim.text.casefold())[:8]]
            if tokens and sum(token in text for token in tokens) >= min(2, len(tokens)):
                if claim.id not in linked_claim_ids:
                    db.add(CoursePageClaim(page_version_id=page.id, claim_id=claim.id))
                    linked_claim_ids.add(claim.id)
                for evidence in db.scalars(select(Evidence).where(Evidence.claim_id == claim.id, Evidence.verified.is_(True))).all():
                    if evidence.source_id and evidence.source_id not in linked_source_ids:
                        db.add(CoursePageSource(page_version_id=page.id, source_id=evidence.source_id))
                        linked_source_ids.add(evidence.source_id)


def ensure_legacy_releases(db: Session) -> int:
    created = 0
    for course in db.scalars(select(CourseVersion).order_by(CourseVersion.created_at)).all():
        exists = db.scalar(select(CourseRelease).where(CourseRelease.legacy_course_version_id == course.id))
        if not exists:
            materialize_course_release(db, course, status="legacy")
            created += 1
    if created:
        db.commit()
    return created


def release_payload(release: CourseRelease, db: Session, include_pages: bool = False) -> dict:
    pages = db.scalars(select(CoursePageVersion).where(CoursePageVersion.release_id == release.id).order_by(CoursePageVersion.position)).all()
    result = {
        "id": release.id,
        "project_id": release.project_id,
        "version": release.version,
        "title": release.title,
        "summary": release.summary,
        "status": release.status,
        "created_at": release.created_at,
        "published_at": release.published_at,
        "page_count": len(pages),
    }
    if include_pages:
        result["pages"] = [page_payload(page, db, compact=True) for page in pages]
    return result


def page_payload(page: CoursePageVersion, db: Session, *, compact: bool = False) -> dict:
    release = db.get(CourseRelease, page.release_id)
    sources = db.scalars(
        select(Source).join(CoursePageSource, CoursePageSource.source_id == Source.id).where(CoursePageSource.page_version_id == page.id)
    ).all()
    claims = db.scalars(
        select(Claim).join(CoursePageClaim, CoursePageClaim.claim_id == Claim.id).where(CoursePageClaim.page_version_id == page.id)
    ).all()
    result = {
        "id": page.id,
        "page_id": page.page_id,
        "parent_page_id": page.parent_page_id,
        "release_id": page.release_id,
        "release_version": release.version if release else None,
        "release_status": release.status if release else None,
        "slug": page.slug,
        "title": page.title,
        "page_type": page.page_type,
        "position": page.position,
        "summary": page.summary,
        "status": page.status,
        "quality_score": page.quality_score,
        "headings": json.loads(page.headings_json or "[]"),
    }
    if not compact:
        result.update(
            {
                "markdown": page.markdown,
                "claims": [{"id": claim.id, "text": claim.text, "provenance": claim.provenance, "status": claim.status} for claim in claims],
                "sources": [{"id": source.id, "title": source.title, "url": source.url} for source in sources],
            }
        )
    return result


def latest_release(db: Session, project_id: str, *, include_drafts: bool = False) -> CourseRelease | None:
    conditions = [CourseRelease.project_id == project_id]
    if include_drafts:
        conditions.append(CourseRelease.status.in_(["published", "legacy", "draft", "awaiting_approval"]))
    else:
        conditions.append(CourseRelease.status.in_(["published", "legacy"]))
    return db.scalar(select(CourseRelease).where(*conditions).order_by(CourseRelease.version.desc()))


def search_course(db: Session, project_id: str, query: str, release_id: str | None = None, limit: int = 20) -> list[dict]:
    release = db.get(CourseRelease, release_id) if release_id else latest_release(db, project_id)
    if not release:
        return []
    pages = db.scalars(select(CoursePageVersion).where(CoursePageVersion.release_id == release.id)).all()
    query_terms = set(re.findall(r"\b\w{3,}\b", query.casefold()))
    query_embedding = embed_text(query)
    ranked = []
    for page in pages:
        body = f"{page.title}\n{page.markdown}"
        terms = set(re.findall(r"\b\w{3,}\b", body.casefold()))
        lexical = len(query_terms & terms) / max(1, len(query_terms))
        body_embedding = embed_text(body[:8000]) if query_embedding else None
        semantic = cosine_similarity(query_embedding, body_embedding) if query_embedding and body_embedding else 0.0
        score = lexical * 0.7 + max(0.0, semantic) * 0.3
        if score > 0:
            ranked.append((score, page))
    return [{**page_payload(page, db, compact=True), "score": round(score, 4)} for score, page in sorted(ranked, key=lambda item: item[0], reverse=True)[:limit]]


def create_documentation_run(
    db: Session,
    project_id: str,
    base_release_id: str | None,
    experiment_budget: int,
    *,
    commit: bool = True,
) -> DocumentationRun:
    base = db.get(CourseRelease, base_release_id) if base_release_id else latest_release(db, project_id)
    if not base or base.project_id != project_id:
        raise ValueError("a published or legacy course release is required")
    active = db.scalar(select(DocumentationRun).where(DocumentationRun.project_id == project_id, DocumentationRun.status.in_(["queued", "running", "awaiting_approval"])))
    if active:
        return active
    run_id = uuid4str()
    run = DocumentationRun(
        id=run_id,
        project_id=project_id,
        base_release_id=base.id,
        status="queued",
        experiment_budget=experiment_budget,
        langgraph_thread_id=f"documentation:{run_id}",
    )
    db.add(run)
    if commit:
        db.commit()
        db.refresh(run)
    else:
        db.flush()
    return run


def documentation_run_payload(run: DocumentationRun, db: Session) -> dict:
    experiments = db.scalars(select(DocumentationExperiment).where(DocumentationExperiment.documentation_run_id == run.id).order_by(DocumentationExperiment.created_at)).all()
    return {
        "id": run.id,
        "project_id": run.project_id,
        "base_release_id": run.base_release_id,
        "candidate_release_id": run.candidate_release_id,
        "status": run.status,
        "experiment_budget": run.experiment_budget,
        "langgraph_thread_id": run.langgraph_thread_id,
        "error": run.error,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "experiments": [
            {
                "id": item.id,
                "page_id": item.page_id,
                "strategy": item.strategy,
                "hypothesis": item.hypothesis,
                "baseline_score": item.baseline_score,
                "candidate_score": item.candidate_score,
                "status": item.status,
                "outcome": item.outcome,
                "model": item.model,
            }
            for item in experiments
        ],
    }


def decide_documentation_run(db: Session, run: DocumentationRun, decision: str, actor: str, note: str | None) -> DocumentationRun:
    if run.status != "awaiting_approval":
        raise ValueError("documentation run is not awaiting approval")
    db.add(ApprovalDecision(documentation_run_id=run.id, decision=decision, actor=actor, note=note))
    if decision == "approve":
        candidate = db.get(CourseRelease, run.candidate_release_id) if run.candidate_release_id else None
        if not candidate:
            raise ValueError("candidate release is missing")
        previous = db.scalars(select(CourseRelease).where(CourseRelease.project_id == run.project_id, CourseRelease.status == "published")).all()
        for release in previous:
            release.status = "superseded"
        candidate.status = "published"
        candidate.published_at = utcnow()
        for page in db.scalars(select(CoursePageVersion).where(CoursePageVersion.release_id == candidate.id)).all():
            page.status = "published"
        run.status = "published"
    else:
        candidate = db.get(CourseRelease, run.candidate_release_id) if run.candidate_release_id else None
        if candidate:
            candidate.status = "discarded"
        run.status = "rejected"
    run.error = None
    run.completed_at = utcnow()
    db.commit()
    db.refresh(run)
    return run


def export_release_zip(db: Session, release: CourseRelease) -> bytes:
    pages = db.scalars(select(CoursePageVersion).where(CoursePageVersion.release_id == release.id).order_by(CoursePageVersion.position)).all()
    index_lines = [f"# {release.title}", "", release.summary, "", "## Contents", ""]
    for page in pages:
        index_lines.append(f"- [{page.title}](pages/{page.slug}.md)")
    combined = [f"# {release.title}", "", release.summary, ""]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", "\n".join(index_lines))
        for page in pages:
            archive.writestr(f"pages/{page.slug}.md", page.markdown)
            combined.extend(["", "---", "", page.markdown])
        archive.writestr("course.md", "\n".join(combined))
        archive.writestr("release.json", json.dumps(release_payload(release, db, include_pages=True), default=str, indent=2))
    return buffer.getvalue()
