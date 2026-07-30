from datetime import timedelta

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import AgentInstallation, Claim, Concept, Project, Relationship, ResearchTask, Submission, utcnow
from app.schemas import AgentTaskResult
from app.services.orchestration import _upsert_graph, claim_task, create_run, fail_task, release_expired_leases


def test_expired_lease_is_requeued():
    with SessionLocal() as db:
        project = Project(title="Test", topic="Test research")
        db.add(project)
        db.commit()
        run = create_run(db, project)
        db.add(AgentInstallation(provider="claude", status="available", mode="headless"))
        db.commit()
        task, _execution = claim_task(db, "runner", "claude")
        task.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        assert release_expired_leases(db) == 1
        db.refresh(task)
        assert task.status == "queued"
        assert task.leased_by is None


@pytest.mark.parametrize("role", ["review", "followup"])
def test_cross_provider_task_falls_back_when_only_one_headless_provider(role):
    with SessionLocal() as db:
        project = Project(title="Test", topic="Test research")
        db.add(project)
        db.commit()
        run = create_run(db, project)
        planning = db.scalar(select(ResearchTask).where(ResearchTask.run_id == run.id))
        planning.status = "completed"
        fallback_task = ResearchTask(
            run_id=run.id,
            role=role,
            objective=f"{role} task",
            status="queued",
            excluded_provider="claude",
            idempotency_key=f"{run.id}:{role}:test",
        )
        db.add(fallback_task)
        db.add(AgentInstallation(provider="claude", status="available", mode="headless"))
        db.commit()
        claimed = claim_task(db, "runner", "claude")
        assert claimed is not None
        assert claimed[0].id == fallback_task.id


def test_rate_limit_failure_requeues_with_backoff():
    with SessionLocal() as db:
        project = Project(title="Rate limited", topic="Research")
        db.add(project)
        db.commit()
        create_run(db, project)
        task, _execution = claim_task(db, "runner", "codex")
        fail_task(db, task, "runner", "HTTP 429 rate limit reached", 1)
        db.refresh(task)
        assert task.status == "queued"
        assert task.available_after is not None
        assert task.max_attempts == 5


def test_verified_single_source_claim_can_populate_graph_pending_review():
    with SessionLocal() as db:
        project = Project(title="Graph", topic="Graph research")
        db.add(project)
        db.commit()
        run = create_run(db, project)
        task = db.scalar(select(ResearchTask).where(ResearchTask.run_id == run.id))
        submission = Submission(task_id=task.id, provider="codex", kind="result", payload_json="{}")
        db.add(submission)
        db.flush()
        claim = Claim(
            project_id=project.id,
            task_id=task.id,
            submission_id=submission.id,
            text="A verified single-source claim",
            provenance="source_supported",
            status="single_source",
        )
        db.add(claim)
        db.flush()
        result = AgentTaskResult.model_validate(
            {
                "summary": "graph result",
                "claims": [],
                "concepts": [
                    {"name": "LoRA", "concept_type": "method", "summary": "Adapters", "claim_indexes": [0]},
                    {"name": "Fine-tuning", "concept_type": "method", "summary": "Training", "claim_indexes": [0]},
                ],
                "relationships": [
                    {"source": "LoRA", "target": "Fine-tuning", "relation_type": "part_of", "claim_indexes": [0]}
                ],
            }
        )
        _upsert_graph(db, project, run, submission, result, [claim])
        db.commit()
        assert db.scalar(select(Concept).where(Concept.project_id == project.id)) is not None
        assert db.scalar(select(Relationship).where(Relationship.project_id == project.id)) is not None
