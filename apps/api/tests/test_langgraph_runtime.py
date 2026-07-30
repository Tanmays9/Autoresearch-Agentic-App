from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import (
    AgentExecution,
    CourseRelease,
    CourseVersion,
    DocumentationExperiment,
    DocumentationRun,
    ExecutionEvent,
    Project,
)
from app.schemas import AgentReviewResult, AgentTaskResult
from app.services.documentation import create_documentation_run, materialize_course_release
from app.services.langgraph_runtime import (
    DocumentationComparison,
    DocumentationScore,
    run_documentation_autoresearch,
    run_research_execution,
)
from app.services.orchestration import build_task_context, claim_task, create_run


class FakeStructuredModel:
    def __init__(self, schema):
        self.schema = schema

    async def ainvoke(self, _messages):
        raw = AIMessage(
            content="structured",
            usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        )
        if self.schema is AgentTaskResult:
            parsed = AgentTaskResult(
                summary="Checkpointed research result",
                subtopics=["one", "two", "three", "four", "five"],
                note_section_markdown="## Result\n\nA provisional section.",
            )
        elif self.schema is AgentReviewResult:
            parsed = AgentReviewResult(summary="Reviewed persisted evidence")
        elif self.schema is DocumentationComparison:
            parsed = DocumentationComparison(
                baseline=DocumentationScore(
                    evidence_coverage=10,
                    curriculum_coverage=10,
                    structure_navigation=8,
                    readability=8,
                    low_duplication=8,
                ),
                candidate=DocumentationScore(
                    evidence_coverage=16,
                    curriculum_coverage=15,
                    structure_navigation=12,
                    readability=12,
                    low_duplication=9,
                ),
                evidence_regression=False,
                unsupported_additions=[],
                summary="The candidate is clearer and remains grounded.",
            )
        else:
            raise AssertionError(f"Unexpected structured schema {self.schema}")
        return {"parsed": parsed, "raw": raw, "parsing_error": None}


class FakeAzureModel:
    def bind_tools(self, _tools):
        return self

    def with_structured_output(self, schema, include_raw=False):
        assert include_raw
        return FakeStructuredModel(schema)

    async def ainvoke(self, _messages):
        return AIMessage(
            content="# Improved documentation\n\n## Explanation\n\nA concise, structured explanation with https://example.com/source.\n\n- Practical example\n- Evaluation note",
            usage_metadata={"input_tokens": 13, "output_tokens": 9, "total_tokens": 22},
        )


@pytest.mark.asyncio
async def test_research_graph_checkpoints_structured_result_and_usage(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.langgraph_runtime.build_azure_model", lambda _deployment: FakeAzureModel())
    monkeypatch.setattr(
        "app.services.langgraph_runtime.settings.langgraph_checkpoint_path",
        str(tmp_path / "research-checkpoints.db"),
    )
    with SessionLocal() as db:
        project = Project(title="Graph test", topic="LangGraph research")
        db.add(project)
        db.commit()
        create_run(db, project, provider_mode="inhouse_azure")
        task, execution = claim_task(db, "langgraph-test-runner", "inhouse_azure", "test")
        context = build_task_context(db, task)
        task_id = task.id
        execution_id = execution.id

    result = await run_research_execution(task_id, execution_id, context)
    assert result["summary"] == "Checkpointed research result"
    assert len(result["subtopics"]) == 5
    assert (tmp_path / "research-checkpoints.db").exists()

    with SessionLocal() as db:
        execution = db.get(AgentExecution, execution_id)
        assert execution.langgraph_thread_id == f"research:{task.run_id}:task:{task_id}"
        assert execution.input_tokens == 24
        assert execution.output_tokens == 16
        assert execution.cost_usd > 0
        assert db.scalar(select(func.count()).select_from(ExecutionEvent).where(ExecutionEvent.execution_id == execution_id)) >= 2


@pytest.mark.asyncio
async def test_documentation_autoresearch_uses_send_fanout_and_stops_for_approval(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.langgraph_runtime.build_azure_model", lambda _deployment: FakeAzureModel())
    monkeypatch.setattr(
        "app.services.langgraph_runtime.settings.langgraph_checkpoint_path",
        str(tmp_path / "documentation-checkpoints.db"),
    )
    with SessionLocal() as db:
        project = Project(title="Documentation", topic="Grounded documentation")
        db.add(project)
        db.flush()
        course = CourseVersion(
            project_id=project.id,
            version=1,
            markdown="# Documentation\n\n## Learning path\n\n### 1. Foundations\n\nA starting point.\n\n## Sources\n",
        )
        db.add(course)
        db.flush()
        release = materialize_course_release(db, course, status="draft")
        db.commit()
        doc_run = create_documentation_run(db, project.id, release.id, 12)
        run_id = doc_run.id

    await run_documentation_autoresearch(run_id)

    with SessionLocal() as db:
        run = db.get(DocumentationRun, run_id)
        experiments = db.scalars(
            select(DocumentationExperiment).where(DocumentationExperiment.documentation_run_id == run_id)
        ).all()
        assert run.status == "awaiting_approval"
        assert run.candidate_release_id
        assert db.get(CourseRelease, run.candidate_release_id).status == "awaiting_approval"
        assert len(experiments) == 12
        assert {item.strategy for item in experiments} == {"evidence", "explanation", "structure"}
        assert all(item.input_tokens > 0 and item.output_tokens > 0 for item in experiments)
        assert all(json.loads(item.candidate_metrics_json).get("evaluator") for item in experiments)
        assert any(item.status == "kept" for item in experiments)
