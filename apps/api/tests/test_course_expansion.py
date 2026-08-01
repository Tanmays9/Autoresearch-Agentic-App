from __future__ import annotations

import json

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    CourseExpansionRequest,
    CourseVersion,
    Project,
    ResearchTask,
    ReviewItem,
)
from app.services.course_expansion import filter_missing_topics
from app.services.documentation import materialize_course_release


def _project_with_course() -> tuple[str, str]:
    with SessionLocal() as db:
        project = Project(title="RAG course", topic="Retrieval augmented generation")
        db.add(project)
        db.flush()
        course = CourseVersion(
            project_id=project.id,
            version=1,
            markdown=(
                "# RAG course\n\n## Learning path\n\n"
                "### 1. Retrieval foundations\n\nRetrievers select relevant passages.\n\n"
                "## Sources\n"
            ),
        )
        db.add(course)
        db.flush()
        materialize_course_release(db, course, status="published")
        db.commit()
        return project.id, project.title


def test_user_can_submit_missing_topic_query_and_agents_receive_gap_context(client):
    project_id, _ = _project_with_course()
    response = client.post(
        f"/api/v1/projects/{project_id}/course/gap-research",
        json={"query": "Add a production failure-recovery tutorial", "max_topics": 3, "source_budget": 25},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "analyzing"
    assert payload["run_id"]
    assert payload["tasks"][0]["role"] == "planning"

    with SessionLocal() as db:
        request = db.get(CourseExpansionRequest, payload["id"])
        planning = db.scalar(
            select(ResearchTask).where(ResearchTask.run_id == request.run_id, ResearchTask.role == "planning")
        )
        context = json.loads(planning.context_json)
        assert context["gap_query"] == "Add a production failure-recovery tutorial"
        assert context["max_missing_topics"] == 3
        assert context["existing_course"]["pages"]


def test_gap_filter_removes_existing_and_duplicate_topics():
    project_id, _ = _project_with_course()
    with SessionLocal() as db:
        missing = filter_missing_topics(
            db,
            project_id,
            [
                "Retrieval foundations",
                "Production failure recovery playbook",
                "A production failure-recovery playbook",
            ],
            5,
        )
        assert missing == ["Production failure recovery playbook"]


def test_review_api_defaults_to_true_conflicts_only(client):
    project_id, _ = _project_with_course()
    with SessionLocal() as db:
        db.add(ReviewItem(project_id=project_id, category="unsupported_claim", status="auto_resolved", message="excluded"))
        db.add(ReviewItem(project_id=project_id, category="conflict", status="open", message="sources disagree"))
        db.commit()
    response = client.get(f"/api/v1/projects/{project_id}/review-items")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["category"] == "conflict"
