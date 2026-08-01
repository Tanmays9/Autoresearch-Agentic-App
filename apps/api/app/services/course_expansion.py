from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Concept,
    CourseExpansionRequest,
    CoursePageVersion,
    Project,
    ResearchRun,
    ResearchTask,
    utcnow,
)
from .documentation import latest_release
from .embeddings import cosine_similarity, embed_text
from .events import add_event


def normalize_gap_query(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _tokens(value: str) -> set[str]:
    return {item for item in normalize_gap_query(value).split() if len(item) > 2}


def course_context(db: Session, project_id: str) -> dict[str, Any]:
    release = latest_release(db, project_id, include_drafts=True)
    pages = (
        db.scalars(
            select(CoursePageVersion)
            .where(CoursePageVersion.release_id == release.id)
            .order_by(CoursePageVersion.position)
        ).all()
        if release
        else []
    )
    concepts = db.scalars(select(Concept).where(Concept.project_id == project_id, Concept.status == "supported")).all()
    return {
        "release": {"id": release.id, "version": release.version, "status": release.status} if release else None,
        "pages": [
            {"slug": page.slug, "title": page.title, "summary": page.summary[:600]}
            for page in pages
        ],
        "concepts": [
            {"name": concept.name, "type": concept.concept_type, "summary": concept.summary[:500]}
            for concept in concepts[:250]
        ],
    }


def _is_duplicate(topic: str, existing_values: list[str], accepted: list[str]) -> bool:
    normalized = normalize_gap_query(topic)
    if not normalized:
        return True
    topic_tokens = _tokens(topic)
    for value in [*existing_values, *accepted]:
        other = normalize_gap_query(value)
        if normalized == other or normalized in other or other in normalized:
            return True
        other_tokens = _tokens(value)
        union = topic_tokens | other_tokens
        if union and len(topic_tokens & other_tokens) / len(union) >= 0.62:
            return True
    vector = embed_text(topic)
    if vector:
        for value in existing_values[:150]:
            other_vector = embed_text(value)
            if other_vector and cosine_similarity(vector, other_vector) >= 0.88:
                return True
    return False


def filter_missing_topics(db: Session, project_id: str, topics: list[str], limit: int = 5) -> list[str]:
    context = course_context(db, project_id)
    existing = [
        f"{page['title']} {page['summary']}"
        for page in context["pages"]
        if not str(page["summary"]).endswith("No verified content is available for this section yet._")
    ]
    existing.extend(f"{item['name']} {item['summary']}" for item in context["concepts"])
    accepted: list[str] = []
    for raw in topics:
        topic = re.sub(r"\s+", " ", str(raw)).strip()[:1000]
        if topic and not _is_duplicate(topic, existing, accepted):
            accepted.append(topic)
        if len(accepted) >= limit:
            break
    return accepted


def create_expansion_request(
    db: Session,
    project: Project,
    query: str,
    *,
    max_topics: int = 5,
    source_budget: int = 100,
) -> CourseExpansionRequest:
    from .orchestration import create_run

    normalized = normalize_gap_query(query)
    active_request = db.scalar(
        select(CourseExpansionRequest).where(
            CourseExpansionRequest.project_id == project.id,
            CourseExpansionRequest.normalized_query == normalized,
            CourseExpansionRequest.status.in_(["queued", "analyzing", "researching"]),
        )
    )
    if active_request:
        return active_request
    active_run = db.scalar(
        select(ResearchRun).where(
            ResearchRun.project_id == project.id,
            ResearchRun.status.in_(["queued", "running", "reviewing"]),
        )
    )
    if active_run:
        raise ValueError("This course already has an active research run. Submit the missing-topic request after it finishes.")

    request = CourseExpansionRequest(
        project_id=project.id,
        query=query.strip(),
        normalized_query=normalized,
        status="analyzing",
    )
    db.add(request)
    db.flush()
    context = course_context(db, project.id)
    objective = (
        "Compare the user's requested coverage with the existing course pages and knowledge graph. "
        f"Return at most {max_topics} precise research subtopics that are genuinely missing. "
        "Do not return topics already substantially covered. Return an empty subtopics list when nothing is missing. "
        f"User gap query: {query.strip()}"
    )
    run = create_run(
        db,
        project,
        task_budget=max(6, max_topics + 3),
        source_budget=source_budget,
        followup_depth_limit=2,
        planning_objective=objective,
        planning_context={
            "topic": project.topic,
            "goal": project.goal,
            "learner_level": project.learner_level,
            "expansion_request_id": request.id,
            "gap_query": query.strip(),
            "max_missing_topics": max_topics,
            "existing_course": context,
        },
    )
    request.run_id = run.id
    request.updated_at = utcnow()
    add_event(db, run.id, "course_gap_submitted", f"Analyzing requested course gap: {query[:240]}")
    db.commit()
    db.refresh(request)
    return request


def expansion_payload(request: CourseExpansionRequest, db: Session) -> dict[str, Any]:
    tasks = (
        db.scalars(select(ResearchTask).where(ResearchTask.run_id == request.run_id).order_by(ResearchTask.created_at)).all()
        if request.run_id
        else []
    )
    stored_task_ids = json.loads(request.task_ids_json or "[]")
    if stored_task_ids and any(task_id is None for task_id in stored_task_ids):
        stored_task_ids = [task.id for task in tasks if task.role in {"research", "followup"}]
    return {
        "id": request.id,
        "project_id": request.project_id,
        "run_id": request.run_id,
        "query": request.query,
        "status": request.status,
        "discovered_topics": json.loads(request.discovered_topics_json or "[]"),
        "task_ids": stored_task_ids,
        "result_summary": request.result_summary,
        "error": request.error,
        "created_at": request.created_at,
        "updated_at": request.updated_at,
        "completed_at": request.completed_at,
        "tasks": [
            {
                "id": task.id,
                "role": task.role,
                "objective": task.objective,
                "status": task.status,
                "assigned_provider": task.assigned_provider,
            }
            for task in tasks
        ],
    }


def mark_expansion_complete(db: Session, run: ResearchRun, summary: str) -> None:
    request = db.scalar(select(CourseExpansionRequest).where(CourseExpansionRequest.run_id == run.id))
    if not request or request.status == "no_missing_topics":
        return
    request.status = "completed"
    request.result_summary = summary
    request.completed_at = utcnow()
    request.updated_at = utcnow()
