from __future__ import annotations

import difflib
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    ApprovalDecision,
    CourseFeedback,
    CourseExpansionRequest,
    CoursePageVersion,
    CourseRelease,
    DocumentationExperiment,
    DocumentationRun,
    Project,
)
from .schemas import (
    CourseCompletionCreate,
    CourseExpansionCreate,
    CourseFeedbackCreate,
    DocumentationDecisionInput,
    DocumentationRunCreate,
    ProjectObjectivePatch,
)
from .services.course_agent import (
    create_completion_iteration,
    create_course_feedback,
    ensure_project_objective,
    feedback_payload,
    objective_payload,
    update_project_objective,
)
from .services.course_expansion import create_expansion_request, expansion_payload
from .services.documentation import (
    create_documentation_run,
    decide_documentation_run,
    documentation_run_payload,
    export_release_zip,
    latest_release,
    page_payload,
    release_payload,
    search_course,
)
from .services.langgraph_runtime import resume_documentation_graph


router = APIRouter(prefix="/api/v1", tags=["documentation"])


def _project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    return project


def _release(db: Session, project_id: str, version: str, *, include_drafts: bool = True) -> CourseRelease:
    _project(db, project_id)
    if version == "latest":
        value = latest_release(db, project_id, include_drafts=include_drafts)
    else:
        try:
            release_version = int(version)
        except ValueError as exc:
            raise HTTPException(422, "release version must be an integer or 'latest'") from exc
        value = db.scalar(
            select(CourseRelease).where(
                CourseRelease.project_id == project_id,
                CourseRelease.version == release_version,
            )
        )
    if not value:
        raise HTTPException(404, "course release not found")
    return value


@router.get("/projects/{project_id}/objective")
def get_project_objective(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = _project(db, project_id)
    value = ensure_project_objective(db, project)
    db.commit()
    db.refresh(value)
    return objective_payload(value)


@router.patch("/projects/{project_id}/objective")
def patch_project_objective(
    project_id: str,
    payload: ProjectObjectivePatch,
    db: Session = Depends(get_db),
) -> dict:
    project = _project(db, project_id)
    value = update_project_objective(db, project, **payload.model_dump())
    return objective_payload(value)


@router.post("/projects/{project_id}/course/completion-runs", status_code=202)
def start_course_completion(
    project_id: str,
    payload: CourseCompletionCreate,
    db: Session = Depends(get_db),
) -> dict:
    project = _project(db, project_id)
    try:
        value = create_completion_iteration(
            db,
            project,
            base_release_id=payload.base_release_id,
            instructions=payload.instructions,
            page_budget=payload.page_budget,
            allow_llm_synthesis=payload.allow_llm_synthesis,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return documentation_run_payload(value, db)


@router.get("/projects/{project_id}/course/feedback")
def list_course_feedback(project_id: str, db: Session = Depends(get_db)) -> dict:
    _project(db, project_id)
    values = db.scalars(
        select(CourseFeedback)
        .where(CourseFeedback.project_id == project_id)
        .order_by(CourseFeedback.created_at.desc())
        .limit(100)
    ).all()
    return {"items": [feedback_payload(item, db) for item in values]}


@router.post("/projects/{project_id}/course/feedback", status_code=202)
def submit_course_feedback(
    project_id: str,
    payload: CourseFeedbackCreate,
    db: Session = Depends(get_db),
) -> dict:
    project = _project(db, project_id)
    try:
        value = create_course_feedback(db, project, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return feedback_payload(value, db)


@router.get("/projects/{project_id}/course/releases")
def list_releases(project_id: str, db: Session = Depends(get_db)) -> dict:
    _project(db, project_id)
    values = db.scalars(
        select(CourseRelease)
        .where(CourseRelease.project_id == project_id)
        .order_by(CourseRelease.version.desc())
    ).all()
    return {
        "items": [release_payload(item, db) for item in values],
        "latest_version": values[0].version if values else None,
        "published_version": next((item.version for item in values if item.status == "published"), None),
    }


@router.post("/projects/{project_id}/course/gap-research", status_code=202)
def submit_course_gap(
    project_id: str,
    payload: CourseExpansionCreate,
    db: Session = Depends(get_db),
) -> dict:
    project = _project(db, project_id)
    try:
        request = create_expansion_request(
            db,
            project,
            payload.query,
            max_topics=payload.max_topics,
            source_budget=payload.source_budget,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return expansion_payload(request, db)


@router.get("/projects/{project_id}/course/gap-research")
def list_course_gaps(project_id: str, db: Session = Depends(get_db)) -> dict:
    _project(db, project_id)
    values = db.scalars(
        select(CourseExpansionRequest)
        .where(CourseExpansionRequest.project_id == project_id)
        .order_by(CourseExpansionRequest.created_at.desc())
        .limit(30)
    ).all()
    return {"items": [expansion_payload(item, db) for item in values]}


@router.get("/course/gap-research/{request_id}")
def get_course_gap(request_id: str, db: Session = Depends(get_db)) -> dict:
    request = db.get(CourseExpansionRequest, request_id)
    if not request:
        raise HTTPException(404, "course gap request not found")
    return expansion_payload(request, db)


@router.get("/projects/{project_id}/course/releases/{version}/tree")
def get_release_tree(project_id: str, version: str, db: Session = Depends(get_db)) -> dict:
    release = _release(db, project_id, version)
    return release_payload(release, db, include_pages=True)


@router.get("/projects/{project_id}/course/releases/{version}/pages/{slug:path}")
def get_course_page(project_id: str, version: str, slug: str, db: Session = Depends(get_db)) -> dict:
    release = _release(db, project_id, version)
    page = db.scalar(
        select(CoursePageVersion).where(
            CoursePageVersion.release_id == release.id,
            CoursePageVersion.slug == slug,
        )
    )
    if not page:
        raise HTTPException(404, "course page not found")
    pages = db.scalars(
        select(CoursePageVersion)
        .where(CoursePageVersion.release_id == release.id)
        .order_by(CoursePageVersion.position)
    ).all()
    index = next((offset for offset, item in enumerate(pages) if item.id == page.id), 0)
    return {
        **page_payload(page, db),
        "previous": page_payload(pages[index - 1], db, compact=True) if index > 0 else None,
        "next": page_payload(pages[index + 1], db, compact=True) if index + 1 < len(pages) else None,
        "release": release_payload(release, db),
    }


@router.get("/projects/{project_id}/course/search")
def course_search(
    project_id: str,
    q: str = Query(min_length=2, max_length=300),
    release_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict:
    _project(db, project_id)
    if release_id:
        release = db.get(CourseRelease, release_id)
        if not release or release.project_id != project_id:
            raise HTTPException(404, "course release not found")
    return {"query": q, "items": search_course(db, project_id, q, release_id, limit)}


@router.get("/projects/{project_id}/course/diff/{draft_version}")
def course_diff(project_id: str, draft_version: str, db: Session = Depends(get_db)) -> dict:
    draft = _release(db, project_id, draft_version)
    doc_run = db.scalar(
        select(DocumentationRun).where(DocumentationRun.candidate_release_id == draft.id)
    )
    base = db.get(CourseRelease, doc_run.base_release_id) if doc_run else db.scalar(
        select(CourseRelease)
        .where(CourseRelease.project_id == project_id, CourseRelease.version < draft.version)
        .order_by(CourseRelease.version.desc())
    )
    if not base:
        raise HTTPException(404, "base release not found")
    base_pages = {
        item.page_id: item
        for item in db.scalars(select(CoursePageVersion).where(CoursePageVersion.release_id == base.id)).all()
    }
    draft_pages = db.scalars(
        select(CoursePageVersion)
        .where(CoursePageVersion.release_id == draft.id)
        .order_by(CoursePageVersion.position)
    ).all()
    pages = []
    for page in draft_pages:
        previous = base_pages.get(page.page_id)
        before = previous.markdown if previous else ""
        diff = "\n".join(
            difflib.unified_diff(
                before.splitlines(),
                page.markdown.splitlines(),
                fromfile=f"release-{base.version}/{page.slug}.md",
                tofile=f"release-{draft.version}/{page.slug}.md",
                lineterm="",
            )
        )
        if diff:
            pages.append(
                {
                    "slug": page.slug,
                    "title": page.title,
                    "before_score": previous.quality_score if previous else 0,
                    "after_score": page.quality_score,
                    "diff": diff,
                }
            )
    draft_page_ids = {page.page_id for page in draft_pages}
    for page_id, previous in base_pages.items():
        if page_id in draft_page_ids:
            continue
        diff = "\n".join(
            difflib.unified_diff(
                previous.markdown.splitlines(),
                [],
                fromfile=f"release-{base.version}/{previous.slug}.md",
                tofile=f"release-{draft.version}/{previous.slug}.md (removed)",
                lineterm="",
            )
        )
        pages.append(
            {
                "slug": previous.slug,
                "title": f"{previous.title} (removed)",
                "before_score": previous.quality_score,
                "after_score": 0,
                "diff": diff,
            }
        )
    return {
        "base": release_payload(base, db),
        "candidate": release_payload(draft, db),
        "pages": pages,
    }


def _release_markdown(db: Session, release: CourseRelease) -> str:
    pages = db.scalars(
        select(CoursePageVersion)
        .where(CoursePageVersion.release_id == release.id)
        .order_by(CoursePageVersion.position)
    ).all()
    lines = [f"# {release.title}", "", release.summary]
    for page in pages:
        lines.extend(["", "---", "", page.markdown])
    return "\n".join(lines).strip() + "\n"


@router.get("/projects/{project_id}/course/releases/{version}/export.md", response_class=PlainTextResponse)
def export_release_markdown(project_id: str, version: str, db: Session = Depends(get_db)) -> str:
    return _release_markdown(db, _release(db, project_id, version))


@router.get("/projects/{project_id}/course/releases/{version}/export.zip")
def export_release_archive(project_id: str, version: str, db: Session = Depends(get_db)) -> Response:
    release = _release(db, project_id, version)
    return Response(
        content=export_release_zip(db, release),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="atlas-course-v{release.version}.zip"'},
    )


@router.get("/projects/{project_id}/course/export.md", response_class=PlainTextResponse)
def export_latest_markdown(project_id: str, db: Session = Depends(get_db)) -> str:
    release = _release(db, project_id, "latest")
    return _release_markdown(db, release)


@router.get("/projects/{project_id}/course/export.zip")
def export_latest_archive(project_id: str, db: Session = Depends(get_db)) -> Response:
    release = _release(db, project_id, "latest")
    return Response(
        content=export_release_zip(db, release),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="atlas-course-v{release.version}.zip"'},
    )


@router.post("/projects/{project_id}/documentation-runs", status_code=202)
def start_documentation_run(
    project_id: str,
    payload: DocumentationRunCreate,
    db: Session = Depends(get_db),
) -> dict:
    _project(db, project_id)
    try:
        value = create_documentation_run(
            db,
            project_id,
            payload.base_release_id,
            payload.experiment_budget,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return documentation_run_payload(value, db)


@router.get("/projects/{project_id}/documentation-runs")
def list_documentation_runs(project_id: str, db: Session = Depends(get_db)) -> dict:
    _project(db, project_id)
    values = db.scalars(
        select(DocumentationRun)
        .where(DocumentationRun.project_id == project_id)
        .order_by(DocumentationRun.created_at.desc())
    ).all()
    return {"items": [documentation_run_payload(item, db) for item in values]}


@router.get("/documentation-runs/{run_id}")
def get_documentation_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    value = db.get(DocumentationRun, run_id)
    if not value:
        raise HTTPException(404, "documentation run not found")
    result = documentation_run_payload(value, db)
    result["approvals"] = [
        {"id": item.id, "decision": item.decision, "actor": item.actor, "note": item.note, "created_at": item.created_at}
        for item in db.scalars(
            select(ApprovalDecision)
            .where(ApprovalDecision.documentation_run_id == run_id)
            .order_by(ApprovalDecision.created_at)
        ).all()
    ]
    return result


@router.get("/documentation-runs/{run_id}/experiments")
def list_experiments(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(DocumentationRun, run_id)
    if not run:
        raise HTTPException(404, "documentation run not found")
    values = db.scalars(
        select(DocumentationExperiment)
        .where(DocumentationExperiment.documentation_run_id == run_id)
        .order_by(DocumentationExperiment.created_at)
    ).all()
    return {
        "items": [
            {
                "id": item.id,
                "page_id": item.page_id,
                "strategy": item.strategy,
                "hypothesis": item.hypothesis,
                "baseline_markdown": item.baseline_markdown,
                "candidate_markdown": item.candidate_markdown,
                "source_snapshot": json.loads(item.source_snapshot_json or "[]"),
                "baseline_metrics": json.loads(item.baseline_metrics_json or "{}"),
                "candidate_metrics": json.loads(item.candidate_metrics_json or "{}"),
                "baseline_score": item.baseline_score,
                "candidate_score": item.candidate_score,
                "status": item.status,
                "outcome": item.outcome,
                "model": item.model,
                "input_tokens": item.input_tokens,
                "output_tokens": item.output_tokens,
                "created_at": item.created_at,
            }
            for item in values
        ]
    }


@router.get("/documentation-experiments/{experiment_id}")
def get_experiment(experiment_id: str, db: Session = Depends(get_db)) -> dict:
    item = db.get(DocumentationExperiment, experiment_id)
    if not item:
        raise HTTPException(404, "documentation experiment not found")
    return {
        "id": item.id,
        "documentation_run_id": item.documentation_run_id,
        "page_id": item.page_id,
        "strategy": item.strategy,
        "hypothesis": item.hypothesis,
        "baseline_markdown": item.baseline_markdown,
        "candidate_markdown": item.candidate_markdown,
        "source_snapshot": json.loads(item.source_snapshot_json or "[]"),
        "baseline_metrics": json.loads(item.baseline_metrics_json or "{}"),
        "candidate_metrics": json.loads(item.candidate_metrics_json or "{}"),
        "baseline_score": item.baseline_score,
        "candidate_score": item.candidate_score,
        "status": item.status,
        "outcome": item.outcome,
        "model": item.model,
        "input_tokens": item.input_tokens,
        "output_tokens": item.output_tokens,
        "created_at": item.created_at,
    }


async def _decide(run_id: str, decision: str, payload: DocumentationDecisionInput, db: Session) -> dict:
    run = db.get(DocumentationRun, run_id)
    if not run:
        raise HTTPException(404, "documentation run not found")
    if run.status != "awaiting_approval":
        raise HTTPException(409, "documentation run is not awaiting approval")
    try:
        await resume_documentation_graph(run_id, decision)
        value = decide_documentation_run(db, run, decision, payload.actor, payload.note)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"LangGraph approval resume failed: {type(exc).__name__}: {str(exc)[:500]}") from exc
    return documentation_run_payload(value, db)


@router.post("/documentation-runs/{run_id}/approve")
async def approve_documentation_run(
    run_id: str,
    payload: DocumentationDecisionInput,
    db: Session = Depends(get_db),
) -> dict:
    return await _decide(run_id, "approve", payload, db)


@router.post("/documentation-runs/{run_id}/reject")
async def reject_documentation_run(
    run_id: str,
    payload: DocumentationDecisionInput,
    db: Session = Depends(get_db),
) -> dict:
    return await _decide(run_id, "reject", payload, db)
