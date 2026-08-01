from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CourseFeedback,
    CoursePage,
    CoursePageVersion,
    CourseRelease,
    Project,
    ProjectObjective,
    utcnow,
)
from .documentation import create_documentation_run, latest_release


DEFAULT_SUCCESS_CRITERIA = [
    "The course covers the prerequisites needed by its intended learner.",
    "Every required topic has a useful explanation or an explicit unresolved note.",
    "The course includes practical guidance, evaluation, troubleshooting, and reference material.",
    "Model-generated material is visibly distinguished from source-supported material.",
]


def ensure_project_objective(db: Session, project: Project) -> ProjectObjective:
    value = db.get(ProjectObjective, project.id)
    if value:
        if not json.loads(value.success_criteria_json or "[]"):
            value.success_criteria_json = json.dumps(DEFAULT_SUCCESS_CRITERIA, ensure_ascii=False)
            value.updated_at = utcnow()
        return value
    value = ProjectObjective(
        project_id=project.id,
        objective=project.goal,
        audience=project.learner_level,
        success_criteria_json=json.dumps(DEFAULT_SUCCESS_CRITERIA, ensure_ascii=False),
        allow_llm_synthesis=True,
    )
    db.add(value)
    db.flush()
    return value


def objective_payload(value: ProjectObjective) -> dict[str, Any]:
    return {
        "project_id": value.project_id,
        "objective": value.objective,
        "audience": value.audience,
        "success_criteria": json.loads(value.success_criteria_json or "[]"),
        "required_topics": json.loads(value.required_topics_json or "[]"),
        "coverage": json.loads(value.coverage_json or "[]"),
        "status": value.status,
        "iteration": value.iteration,
        "completion_score": value.completion_score,
        "allow_llm_synthesis": value.allow_llm_synthesis,
        "last_reviewed_at": value.last_reviewed_at,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def update_project_objective(
    db: Session,
    project: Project,
    *,
    objective: str | None = None,
    audience: str | None = None,
    success_criteria: list[str] | None = None,
    allow_llm_synthesis: bool | None = None,
) -> ProjectObjective:
    value = ensure_project_objective(db, project)
    if objective is not None:
        value.objective = objective.strip()
        project.goal = value.objective
    if audience is not None:
        value.audience = audience.strip()
        project.learner_level = value.audience
    if success_criteria is not None:
        value.success_criteria_json = json.dumps([item.strip() for item in success_criteria if item.strip()], ensure_ascii=False)
    if allow_llm_synthesis is not None:
        value.allow_llm_synthesis = allow_llm_synthesis
    value.status = "active"
    value.updated_at = utcnow()
    db.commit()
    db.refresh(value)
    return value


def create_completion_iteration(
    db: Session,
    project: Project,
    *,
    base_release_id: str | None,
    instructions: str | None,
    page_budget: int,
    allow_llm_synthesis: bool,
):
    objective = ensure_project_objective(db, project)
    base = db.get(CourseRelease, base_release_id) if base_release_id else latest_release(db, project.id, include_drafts=True)
    if not base or base.project_id != project.id:
        raise ValueError("Create a course release before asking the completion agent to review it.")
    objective.allow_llm_synthesis = allow_llm_synthesis
    objective.status = "reviewing"
    objective.updated_at = utcnow()
    run = create_documentation_run(
        db,
        project.id,
        base.id,
        page_budget,
        run_type="completion",
        instructions=instructions,
        allow_llm_synthesis=allow_llm_synthesis,
        dedupe_active=True,
        commit=False,
    )
    db.commit()
    db.refresh(run)
    return run


def create_course_feedback(
    db: Session,
    project: Project,
    *,
    kind: str,
    message: str,
    release_id: str | None,
    page_id: str | None,
    allow_llm_synthesis: bool,
) -> CourseFeedback:
    release = db.get(CourseRelease, release_id) if release_id else latest_release(db, project.id, include_drafts=True)
    if not release or release.project_id != project.id:
        raise ValueError("course release not found")
    if page_id:
        page = db.get(CoursePage, page_id)
        page_version = db.scalar(
            select(CoursePageVersion).where(
                CoursePageVersion.release_id == release.id,
                CoursePageVersion.page_id == page_id,
            )
        )
        if not page or page.project_id != project.id or not page_version:
            raise ValueError("course page not found in this release")
    feedback = CourseFeedback(
        project_id=project.id,
        release_id=release.id,
        page_id=page_id,
        kind=kind,
        message=message.strip(),
        allow_llm_synthesis=allow_llm_synthesis,
        status="queued",
    )
    db.add(feedback)
    db.flush()
    run = create_documentation_run(
        db,
        project.id,
        release.id,
        12 if kind == "restructure" and not page_id else 3,
        run_type="feedback",
        instructions=f"{kind.upper()} feedback: {message.strip()}",
        allow_llm_synthesis=allow_llm_synthesis,
        feedback_id=feedback.id,
        dedupe_active=False,
        commit=False,
    )
    feedback.documentation_run_id = run.id
    db.commit()
    db.refresh(feedback)
    return feedback


def feedback_payload(value: CourseFeedback, db: Session) -> dict[str, Any]:
    page_title = None
    if value.page_id and value.release_id:
        page = db.scalar(
            select(CoursePageVersion).where(
                CoursePageVersion.release_id == value.release_id,
                CoursePageVersion.page_id == value.page_id,
            )
        )
        page_title = page.title if page else None
    return {
        "id": value.id,
        "project_id": value.project_id,
        "release_id": value.release_id,
        "page_id": value.page_id,
        "page_title": page_title,
        "kind": value.kind,
        "message": value.message,
        "allow_llm_synthesis": value.allow_llm_synthesis,
        "status": value.status,
        "documentation_run_id": value.documentation_run_id,
        "result_summary": value.result_summary,
        "error": value.error,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "completed_at": value.completed_at,
    }
