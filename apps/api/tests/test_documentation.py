from __future__ import annotations

import io
import zipfile

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    CoursePageVersion,
    CourseRelease,
    CourseVersion,
    DocumentationRun,
    Project,
)
from app.services.documentation import (
    create_documentation_run,
    documentation_metrics,
    materialize_course_release,
)
from app.services.langgraph_runtime import (
    ROLE_TOOLS,
    SAFE_TOOLS,
    _tool_definitions,
    _candidate_prompt,
    build_approval_graph,
    resume_documentation_graph,
)


COURSE_MARKDOWN = """# Retrieval-Augmented Generation

> Topic: RAG evaluation

## Learning path

Start with retrieval and grounded generation.

### 1. Retrieval foundations

Retrievers select source passages before generation.[^1]

#### Evidence-backed claims

- Retrieval supplies context to a generator.[^1]

### 2. Evaluation

Measure retrieval separately from answer quality.[^1]

## Unresolved claims

- Which benchmark best predicts production quality?

## Sources

[^1]: [Example source](https://example.com/rag)
"""


def create_release(status: str = "draft") -> tuple[str, str]:
    with SessionLocal() as db:
        project = Project(title="RAG Course", topic="RAG evaluation")
        db.add(project)
        db.flush()
        course = CourseVersion(project_id=project.id, version=1, markdown=COURSE_MARKDOWN)
        db.add(course)
        db.flush()
        release = materialize_course_release(db, course, status=status)
        db.commit()
        return project.id, release.id


def test_documentation_release_is_indexed_hierarchical_searchable_and_exportable(client):
    project_id, release_id = create_release()

    releases = client.get(f"/api/v1/projects/{project_id}/course/releases")
    assert releases.status_code == 200
    assert releases.json()["items"][0]["status"] == "draft"

    tree = client.get(f"/api/v1/projects/{project_id}/course/releases/latest/tree")
    assert tree.status_code == 200
    pages = tree.json()["pages"]
    assert len(pages) >= 13
    child = next(page for page in pages if page["slug"].startswith("core-concepts/"))
    assert child["parent_page_id"]

    page = client.get(
        f"/api/v1/projects/{project_id}/course/releases/{tree.json()['version']}/pages/{child['slug']}"
    )
    assert page.status_code == 200
    assert "Retrieval" in page.json()["markdown"] or "Evaluation" in page.json()["markdown"]
    assert "previous" in page.json() and "next" in page.json()

    search = client.get(
        f"/api/v1/projects/{project_id}/course/search",
        params={"q": "retrieval context", "release_id": release_id},
    )
    assert search.status_code == 200
    assert search.json()["items"]

    archive = client.get(f"/api/v1/projects/{project_id}/course/releases/latest/export.zip")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as value:
        names = value.namelist()
        assert "README.md" in names
        assert "course.md" in names
        assert any(name.startswith("pages/core-concepts/") for name in names)


def test_agent_settings_are_safe_and_editable(client):
    response = client.get("/api/v1/settings/agents")
    assert response.status_code == 200
    payload = response.json()
    assert "api_key" not in str(payload).casefold()
    assert payload["azure"]["research_deployment"]
    assert payload["azure"]["single_model_policy"] is True
    assert payload["azure"]["research_deployment"] == "gpt-5.6-sol"
    assert payload["azure"]["reasoning_deployment"] == "gpt-5.6-sol"
    assert payload["auto_publish_documentation"] is False
    assert payload["documentation_approval_required"] is True

    updated = client.patch(
        "/api/v1/settings/agents",
        json={"inhouse_agent_concurrency": 4, "provider_mode": "inhouse_azure"},
    )
    assert updated.status_code == 200
    assert updated.json()["inhouse_agent_concurrency"] == 4


def test_documentation_approval_is_explicit(monkeypatch, client):
    project_id, base_release_id = create_release(status="published")
    with SessionLocal() as db:
        base = db.get(CourseRelease, base_release_id)
        candidate = CourseRelease(
            project_id=project_id,
            version=base.version + 1,
            title=base.title,
            summary="Candidate",
            status="awaiting_approval",
        )
        db.add(candidate)
        db.flush()
        for page in db.scalars(select(CoursePageVersion).where(CoursePageVersion.release_id == base.id)).all():
            db.add(
                CoursePageVersion(
                    page_id=page.page_id,
                    release_id=candidate.id,
                    parent_page_id=page.parent_page_id,
                    slug=page.slug,
                    title=page.title,
                    page_type=page.page_type,
                    position=page.position,
                    markdown=page.markdown + "\n\nImproved explanation.",
                    summary=page.summary,
                    status="awaiting_approval",
                    headings_json=page.headings_json,
                    quality_score=page.quality_score + 5,
                )
            )
        run = DocumentationRun(
            project_id=project_id,
            base_release_id=base.id,
            candidate_release_id=candidate.id,
            status="awaiting_approval",
            experiment_budget=12,
            langgraph_thread_id="documentation:test-approval",
        )
        db.add(run)
        db.commit()
        run_id = run.id
        candidate_id = candidate.id

    async def fake_resume(_run_id: str, decision: str) -> None:
        assert decision == "approve"

    monkeypatch.setattr("app.docs_api.resume_documentation_graph", fake_resume)
    approved = client.post(
        f"/api/v1/documentation-runs/{run_id}/approve",
        json={"actor": "test-user", "note": "Reviewed all changes"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "published"
    with SessionLocal() as db:
        assert db.get(CourseRelease, candidate_id).status == "published"
        assert db.get(CourseRelease, base_release_id).status == "superseded"


def test_documentation_metrics_follow_weighted_hundred_point_rubric():
    value = documentation_metrics(
        "# Page\n\n## Concept\n\nA short readable explanation with a source https://example.com.\n\n- A practical step."
    )
    assert set(value) == {
        "evidence_coverage",
        "curriculum_coverage",
        "structure_navigation",
        "readability",
        "low_duplication",
        "total",
    }
    assert 0 <= value["total"] <= 100
    assert value["evidence_coverage"] <= 35
    assert value["curriculum_coverage"] <= 25
    assert value["structure_navigation"] <= 15
    assert value["readability"] <= 15
    assert value["low_duplication"] <= 10


def test_project_objective_completion_agent_and_feedback_are_durable(client):
    project_id, release_id = create_release(status="draft")
    objective = client.get(f"/api/v1/projects/{project_id}/objective")
    assert objective.status_code == 200
    assert objective.json()["allow_llm_synthesis"] is True

    updated = client.patch(
        f"/api/v1/projects/{project_id}/objective",
        json={
            "objective": "Create a complete practical RAG evaluation course",
            "success_criteria": ["Cover retrieval and generation evaluation", "Include troubleshooting"],
            "allow_llm_synthesis": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["objective"].startswith("Create a complete")

    completion = client.post(
        f"/api/v1/projects/{project_id}/course/completion-runs",
        json={
            "base_release_id": release_id,
            "page_budget": 20,
            "allow_llm_synthesis": True,
            "instructions": "Review every page and fill missing material.",
        },
    )
    assert completion.status_code == 202, completion.text
    assert completion.json()["run_type"] == "completion"
    assert completion.json()["allow_llm_synthesis"] is True

    page_id = client.get(f"/api/v1/projects/{project_id}/course/releases/latest/tree").json()["pages"][0]["page_id"]
    feedback = client.post(
        f"/api/v1/projects/{project_id}/course/feedback",
        json={
            "kind": "improve",
            "message": "Add a concrete worked example and simplify the introduction.",
            "release_id": release_id,
            "page_id": page_id,
            "allow_llm_synthesis": True,
        },
    )
    assert feedback.status_code == 202, feedback.text
    assert feedback.json()["status"] == "queued"
    assert feedback.json()["documentation_run_id"]
    history = client.get(f"/api/v1/projects/{project_id}/course/feedback").json()["items"]
    assert history[0]["message"].startswith("Add a concrete")
    assert history[0]["page_title"]


def test_tool_permissions_are_role_specific():
    planning = {tool.name for tool in _tool_definitions("project", "run", "task", "execution", "planning")}
    review = {tool.name for tool in _tool_definitions("project", "run", "task", "execution", "review")}
    research = {tool.name for tool in _tool_definitions("project", "run", "task", "execution", "research")}
    assert planning == ROLE_TOOLS["planning"]
    assert review == ROLE_TOOLS["review"]
    assert research == SAFE_TOOLS
    assert "search_web" not in review
    assert not {"shell", "filesystem", "sql", "fetch_url"} & research


def test_flexible_synthesis_prompt_requires_visible_provenance_and_forbids_fake_citations():
    prompt = _candidate_prompt(
        title="Evaluation",
        markdown="# Evaluation\n\n_No content yet._",
        strategy="completion",
        source_context="[]",
        objective="Teach practical evaluation",
        instructions="Fill the page",
        allow_llm_synthesis=True,
    )
    assert "LLM synthesis" in prompt
    assert "Never invent a source" in prompt
    assert "Teach practical evaluation" in prompt


@pytest.mark.asyncio
async def test_langgraph_approval_interrupt_can_resume(tmp_path):
    path = tmp_path / "checkpoints.db"
    async with AsyncSqliteSaver.from_conn_string(str(path)) as checkpointer:
        graph = build_approval_graph(checkpointer)
        config = {"configurable": {"thread_id": "documentation:test-resume"}}
        interrupted = await graph.ainvoke({"documentation_run_id": "run-1"}, config=config)
        assert interrupted.get("__interrupt__")
        resumed = await graph.ainvoke(Command(resume="approve"), config=config)
        assert resumed["decision"] == "approve"


@pytest.mark.asyncio
async def test_legacy_candidate_without_interrupt_is_seeded_before_resume(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.langgraph_runtime.settings.langgraph_checkpoint_path",
        str(tmp_path / "legacy-approval-checkpoints.db"),
    )
    project_id, release_id = create_release(status="draft")
    with SessionLocal() as db:
        run = DocumentationRun(
            project_id=project_id,
            base_release_id=release_id,
            status="awaiting_approval",
            experiment_budget=12,
            langgraph_thread_id="documentation:legacy-without-interrupt",
        )
        db.add(run)
        db.commit()
        run_id = run.id

    await resume_documentation_graph(run_id, "approve")
