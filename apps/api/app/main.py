from __future__ import annotations

import asyncio
import difflib
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import SessionLocal, get_db, init_db
from .mcp import graph_payload, router as mcp_router, serialize_task
from .docs_api import router as docs_router
from .models import (
    AgentInstallation,
    AgentExecution,
    Claim,
    Concept,
    CourseVersion,
    CourseExpansionRequest,
    CoursePageVersion,
    CourseRelease,
    CrawlJob,
    CrawlTarget,
    Evidence,
    ExecutionEvent,
    Project,
    DocumentationRun,
    Relationship,
    ResearchSettings,
    ResearchRun,
    ResearchTask,
    ReviewItem,
    RunnerRegistration,
    RunEvent,
    Source,
    SourceChunk,
    SourceSnapshot,
    Submission,
    utcnow,
)
from .schemas import (
    AgentSettingsPatch,
    DocumentationDecisionInput,
    DocumentationRunCreate,
    ExecutionEventBatch,
    KiroLaunchClaim,
    ManualSourceCreate,
    ProjectCreate,
    PublicProject,
    ResearchStart,
    ResearchSettingsPatch,
    ReviewDecision,
    RunnerHeartbeat,
    RunnerRegister,
    TaskClaimRequest,
    TaskFailRequest,
    TaskHeartbeat,
    TaskRelease,
    TaskSubmitRequest,
)
from .security import require_local_token
from .services.events import add_event
from .services.orchestration import (
    _assemble_course,
    accept_submission,
    build_draft,
    build_task_context,
    claim_kiro_launch,
    claim_task,
    create_run,
    create_task,
    fail_task,
    heartbeat_task,
    report_kiro_launch,
    request_codex_research,
    request_kiro_research,
)
from .services.bootstrap import bootstrap_queued_runs
from .services.course_agent import ensure_project_objective, objective_payload
from .services.crawler import add_crawl_targets, ensure_crawl_job, get_research_settings
from .services.documentation import (
    create_documentation_run,
    decide_documentation_run,
    documentation_run_payload,
    ensure_legacy_releases,
    export_release_zip,
    latest_release,
    page_payload,
    release_payload,
    search_course,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with SessionLocal() as db:
        ensure_legacy_releases(db)
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(mcp_router)
app.include_router(docs_router)


def project_dict(project: Project) -> dict:
    return PublicProject.model_validate(project).model_dump(mode="json")


def run_dict(run: ResearchRun) -> dict:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "status": run.status,
        "task_budget": run.task_budget,
        "source_budget": run.source_budget,
        "provider_mode": run.provider_mode,
        "langgraph_thread_id": run.langgraph_thread_id,
        "token_budget": run.token_budget,
        "tokens_used": run.tokens_used,
        "cost_budget_usd": run.cost_budget_usd,
        "cost_used_usd": run.cost_used_usd,
        "tasks_created": run.tasks_created,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "stop_reason": run.stop_reason,
    }


def project_run_history(db: Session, project_id: str) -> list[dict]:
    """Return every research run without collapsing earlier follow-up history.

    A course-gap request creates a new ResearchRun.  The project detail endpoint
    historically exposed only the newest run, which made older tasks appear to
    have been deleted even though they remained durable in SQLite.  Keep the
    latest-run fields for backwards compatibility while also exposing a complete
    per-project history that clients can browse.
    """
    runs = db.scalars(
        select(ResearchRun)
        .where(ResearchRun.project_id == project_id)
        .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
    ).all()
    requests = db.scalars(
        select(CourseExpansionRequest).where(CourseExpansionRequest.project_id == project_id)
    ).all()
    request_by_run = {item.run_id: item for item in requests if item.run_id}

    history: list[dict] = []
    for position, run in enumerate(runs):
        tasks = db.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .order_by(ResearchTask.created_at, ResearchTask.id)
        ).all()
        gap_request = request_by_run.get(run.id)
        history.append(
            {
                **run_dict(run),
                "sequence": len(runs) - position,
                "kind": "course_gap" if gap_request else "research",
                "label": gap_request.query if gap_request else ("Initial research" if position == len(runs) - 1 else "Research run"),
                "task_count": len(tasks),
                "completed_task_count": sum(task.status == "completed" for task in tasks),
                "tasks": [serialize_task(task, db) for task in tasks],
            }
        )
    return history


def execution_dict(execution: AgentExecution, db: Session, *, include_events: bool = False) -> dict:
    task = db.get(ResearchTask, execution.task_id)
    submission = db.scalar(select(Submission).where(Submission.task_id == execution.task_id).order_by(Submission.created_at.desc()))
    payload = {
        "id": execution.id,
        "task_id": execution.task_id,
        "runner_id": execution.runner_id,
        "provider": execution.provider,
        "status": execution.status,
        "started_at": execution.started_at,
        "completed_at": execution.completed_at,
        "last_heartbeat_at": execution.last_heartbeat_at,
        "cancel_requested_at": execution.cancel_requested_at,
        "exit_code": execution.exit_code,
        "diagnostic": execution.diagnostic,
        "output_bytes": execution.output_bytes,
        "model": execution.model,
        "langgraph_thread_id": execution.langgraph_thread_id,
        "input_tokens": execution.input_tokens,
        "output_tokens": execution.output_tokens,
        "cost_usd": execution.cost_usd,
        "tool_calls": json.loads(execution.tool_calls_json or "[]"),
        "task": serialize_task(task, db) if task else None,
        "result": json.loads(submission.payload_json) if submission else None,
    }
    if include_events:
        events = db.scalars(
            select(ExecutionEvent).where(ExecutionEvent.execution_id == execution.id).order_by(ExecutionEvent.sequence)
        ).all()
        payload["events"] = [
            {
                "id": item.id,
                "sequence": item.sequence,
                "stream": item.stream,
                "event_type": item.event_type,
                "content": item.content,
                "created_at": item.created_at,
            }
            for item in events
        ]
    return payload


def _sanitize_execution_content(content: str, event_type: str) -> str:
    if "reasoning" in event_type.casefold():
        return "[private reasoning event omitted]"
    try:
        parsed = json.loads(content)
        item_type = str(parsed.get("item", {}).get("type", "")) if isinstance(parsed, dict) else ""
        if "reasoning" in item_type.casefold():
            return "[private reasoning event omitted]"
    except Exception:
        pass
    value = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s\"']+", r"\1[redacted]", content)
    value = re.sub(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;\"']+", r"\1[redacted]", value)
    value = re.sub(r"[A-Za-z]:(?:(?:\\\\)|\\)[^\r\n\"']+", "<local-path>", value)
    return value


def review_detail(item: ReviewItem, db: Session) -> dict:
    claim = db.get(Claim, item.claim_id) if item.claim_id else None
    submission = db.get(Submission, item.submission_id) if item.submission_id else None
    task = db.get(ResearchTask, submission.task_id) if submission else None
    execution = db.scalar(
        select(AgentExecution).where(AgentExecution.task_id == task.id).order_by(AgentExecution.started_at.desc())
    ) if task else None
    evidences = db.scalars(select(Evidence).where(Evidence.claim_id == claim.id)) .all() if claim else []
    concepts = db.scalars(select(Concept).where(Concept.supporting_claim_id == claim.id)).all() if claim else []
    relationships = db.scalars(select(Relationship).where(Relationship.supporting_claim_id == claim.id)).all() if claim else []
    submission_payload = json.loads(submission.payload_json) if submission else {}
    return {
        "id": item.id,
        "category": item.category,
        "status": item.status,
        "message": item.message,
        "decision": item.decision,
        "created_at": item.created_at,
        "claim": {
            "id": claim.id,
            "text": claim.text,
            "provenance": claim.provenance,
            "status": claim.status,
        } if claim else None,
        "evidence": [
            {
                "id": evidence.id,
                "quote": evidence.quote,
                "locator": evidence.locator,
                "verified": evidence.verified,
                "error": evidence.error,
                "source": (
                    lambda source: {"id": source.id, "url": source.url, "title": source.title, "status": source.status}
                    if source else None
                )(db.get(Source, evidence.source_id) if evidence.source_id else None),
            }
            for evidence in evidences
        ],
        "submission": {
            "id": submission.id,
            "provider": submission.provider,
            "validation_status": submission.validation_status,
            "summary": submission_payload.get("summary", ""),
            "note_section_markdown": submission_payload.get("note_section_markdown", ""),
        } if submission else None,
        "task": serialize_task(task, db) if task else None,
        "execution": execution_dict(execution, db) if execution else None,
        "concepts": [{"id": value.id, "name": value.name, "type": value.concept_type} for value in concepts],
        "relationships": [
            {
                "id": value.id,
                "type": value.relation_type,
                "source": (db.get(Concept, value.source_concept_id).name if db.get(Concept, value.source_concept_id) else ""),
                "target": (db.get(Concept, value.target_concept_id).name if db.get(Concept, value.target_concept_id) else ""),
            }
            for value in relationships
        ],
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "atlas-research"}


@app.get("/api/v1/settings/research")
def research_settings(db: Session = Depends(get_db)) -> dict:
    value = get_research_settings(db)
    return {
        "codex_concurrency": value.codex_concurrency,
        "codex_web_research": value.codex_web_research,
        "default_source_budget": value.default_source_budget,
        "crawler_concurrency": value.crawler_concurrency,
        "crawler_per_domain": value.crawler_per_domain,
        "updated_at": value.updated_at,
    }


@app.patch("/api/v1/settings/research")
def update_research_settings(payload: ResearchSettingsPatch, db: Session = Depends(get_db)) -> dict:
    value = get_research_settings(db)
    for key, item in payload.model_dump(exclude_none=True).items():
        setattr(value, key, item)
    value.updated_at = utcnow()
    db.commit()
    db.refresh(value)
    return research_settings(db)


def agent_settings_payload(db: Session) -> dict:
    value = get_research_settings(db)
    return {
        "provider_mode": value.provider_mode,
        "inhouse_agent_concurrency": value.inhouse_agent_concurrency,
        "inhouse_tool_rounds": value.inhouse_tool_rounds,
        "default_task_budget": value.default_task_budget,
        "documentation_experiment_budget": value.documentation_experiment_budget,
        "azure_token_budget": value.azure_token_budget,
        "azure_cost_budget_usd": value.azure_cost_budget_usd,
        "codex_fallback": value.codex_fallback,
        "auto_accept_verified_single_source": value.auto_accept_verified_single_source,
        "auto_resolve_evidence_exceptions": value.auto_resolve_evidence_exceptions,
        # Compatibility signal for older clients. This is intentionally
        # immutable: publishing always requires a human approval interrupt.
        "auto_publish_documentation": False,
        "documentation_approval_required": True,
        "azure": {
            "ready": settings.azure_ready,
            "single_model_policy": True,
            "research_deployment": settings.azure_reasoning_deployment,
            "reasoning_deployment": settings.azure_reasoning_deployment,
            "api_version": settings.azure_openai_api_version,
        },
        "brave_ready": bool(settings.brave_search_api_key),
    }


@app.get("/api/v1/settings/agents")
def get_agent_settings(db: Session = Depends(get_db)) -> dict:
    return agent_settings_payload(db)


@app.patch("/api/v1/settings/agents")
def update_agent_settings(payload: AgentSettingsPatch, db: Session = Depends(get_db)) -> dict:
    value = get_research_settings(db)
    for key, item in payload.model_dump(exclude_none=True).items():
        setattr(value, key, item)
    value.updated_at = utcnow()
    db.commit()
    return agent_settings_payload(db)


@app.post("/api/v1/settings/agents/test")
async def test_agent_settings() -> dict:
    if not settings.azure_ready:
        raise HTTPException(409, "Azure inference is not configured")
    try:
        from langchain_core.messages import HumanMessage
        from .services.langgraph_runtime import build_azure_model

        model = build_azure_model(settings.azure_reasoning_deployment)
        response = await model.ainvoke([HumanMessage(content="Reply with exactly: Atlas ready")])
        return {"ready": True, "deployment": settings.azure_reasoning_deployment, "response": str(response.content)[:120]}
    except Exception as exc:
        raise HTTPException(502, f"Azure readiness test failed: {type(exc).__name__}: {str(exc)[:500]}") from exc


@app.get("/api/v1/projects")
def list_projects(db: Session = Depends(get_db)) -> list[dict]:
    projects = db.scalars(select(Project).order_by(Project.updated_at.desc())).all()
    return [project_dict(item) for item in projects]


@app.post("/api/v1/projects", status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> dict:
    project = Project(
        title=payload.title,
        topic=payload.topic,
        goal=payload.goal,
        learner_level=payload.learner_level,
    )
    db.add(project)
    db.flush()
    objective = ensure_project_objective(db, project)
    db.commit()
    db.refresh(project)
    db.refresh(objective)
    run = create_run(db, project) if payload.start_immediately else None
    return {"project": project_dict(project), "objective": objective_payload(objective), "run": run_dict(run) if run else None}


@app.get("/api/v1/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    objective = ensure_project_objective(db, project)
    db.commit()
    latest_run = db.scalar(
        select(ResearchRun).where(ResearchRun.project_id == project.id).order_by(ResearchRun.created_at.desc())
    )
    tasks = (
        db.scalars(select(ResearchTask).where(ResearchTask.run_id == latest_run.id).order_by(ResearchTask.created_at)).all()
        if latest_run
        else []
    )
    submissions = []
    if latest_run:
        values = db.scalars(
            select(Submission)
            .join(ResearchTask, ResearchTask.id == Submission.task_id)
            .where(ResearchTask.run_id == latest_run.id)
            .order_by(Submission.created_at)
        ).all()
        submissions = [
            {
                "id": item.id,
                "task_id": item.task_id,
                "provider": item.provider,
                "kind": item.kind,
                "validation_status": item.validation_status,
                "same_provider_review": item.same_provider_review,
                "payload": json.loads(item.payload_json),
                "created_at": item.created_at,
            }
            for item in values
        ]
    sources = db.scalars(select(Source).where(Source.project_id == project.id).order_by(Source.created_at.desc())).all()
    reviews = db.scalars(
        select(ReviewItem)
        .where(ReviewItem.project_id == project.id, ReviewItem.status == "open")
        .order_by(ReviewItem.created_at.desc())
    ).all()
    course = db.scalar(
        select(CourseVersion).where(CourseVersion.project_id == project.id).order_by(CourseVersion.version.desc())
    )
    events = (
        db.scalars(select(RunEvent).where(RunEvent.run_id == latest_run.id).order_by(RunEvent.id.desc()).limit(50)).all()
        if latest_run
        else []
    )
    return {
        "project": project_dict(project),
        "objective": objective_payload(objective),
        "run": run_dict(latest_run) if latest_run else None,
        "run_history": project_run_history(db, project.id),
        "tasks": [serialize_task(item, db) for item in tasks],
        "submissions": submissions,
        "sources": [
            {"id": item.id, "url": item.url, "title": item.title, "status": item.status, "trust_level": item.trust_level}
            for item in sources
        ],
        "reviews": [
            {
                "id": item.id,
                "category": item.category,
                "status": item.status,
                "message": item.message,
                "decision": item.decision,
                "claim_id": item.claim_id,
                "created_at": item.created_at,
            }
            for item in reviews
        ],
        "events": [
            {"id": item.id, "type": item.event_type, "message": item.message, "created_at": item.created_at}
            for item in reversed(events)
        ],
        "graph": graph_payload(db, project.id),
        "course": {"version": course.version, "markdown": course.markdown, "created_at": course.created_at} if course else None,
    }


@app.get("/api/v1/projects/{project_id}/research-runs")
def list_project_research_runs(project_id: str, db: Session = Depends(get_db)) -> dict:
    if not db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    return {"items": project_run_history(db, project_id)}


@app.post("/api/v1/projects/{project_id}/research-runs", status_code=202)
def start_research(project_id: str, payload: ResearchStart, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    run = create_run(db, project, payload.task_budget, payload.source_budget, payload.followup_depth_limit, payload.provider_mode)
    return run_dict(run)


@app.post("/api/v1/projects/{project_id}/research-with-kiro", status_code=202)
def research_with_kiro(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    try:
        run, task = request_kiro_research(db, project)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run": run_dict(run), "task": serialize_task(task, db), "message": "Kiro launch requested"}


@app.post("/api/v1/projects/{project_id}/research-with-codex", status_code=202)
def research_with_codex(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    try:
        run, task = request_codex_research(db, project)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run": run_dict(run), "task": serialize_task(task, db), "message": "Codex research queued"}


@app.get("/api/v1/research-runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return {**run_dict(run), "tasks": [serialize_task(task, db) for task in run.tasks]}


@app.post("/api/v1/research-runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    run.status = "cancelled"
    run.stop_reason = "Cancelled by user"
    run.completed_at = utcnow()
    for task in run.tasks:
        if task.status in {"queued", "leased", "running"}:
            task.status = "cancelled"
    db.commit()
    return run_dict(run)


@app.get("/api/v1/research-runs/{run_id}/events")
async def run_events(run_id: str, db: Session = Depends(get_db)):
    if not db.get(ResearchRun, run_id):
        raise HTTPException(404, "run not found")

    async def stream():
        last_id = 0
        while True:
            with SessionLocal() as stream_db:
                events = stream_db.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.id > last_id).order_by(RunEvent.id)
                ).all()
                for item in events:
                    last_id = item.id
                    yield f"id: {item.id}\nevent: {item.event_type}\ndata: {json.dumps({'message': item.message, 'created_at': str(item.created_at)})}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/v1/research-runs/{run_id}/executions")
def list_executions(run_id: str, db: Session = Depends(get_db)) -> dict:
    if not db.get(ResearchRun, run_id):
        raise HTTPException(404, "run not found")
    values = db.scalars(
        select(AgentExecution)
        .join(ResearchTask, ResearchTask.id == AgentExecution.task_id)
        .where(ResearchTask.run_id == run_id)
        .order_by(AgentExecution.started_at.desc())
    ).all()
    return {"executions": [execution_dict(item, db) for item in values]}


@app.get("/api/v1/executions/{execution_id}")
def get_execution(execution_id: str, db: Session = Depends(get_db)) -> dict:
    execution = db.get(AgentExecution, execution_id)
    if not execution:
        raise HTTPException(404, "execution not found")
    return execution_dict(execution, db, include_events=True)


@app.get("/api/v1/executions/{execution_id}/events")
async def execution_events(
    execution_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
):
    if not db.get(AgentExecution, execution_id):
        raise HTTPException(404, "execution not found")
    initial_sequence = int(last_event_id or 0)

    async def stream():
        sequence = initial_sequence
        idle_after_terminal = 0
        while True:
            with SessionLocal() as stream_db:
                values = stream_db.scalars(
                    select(ExecutionEvent)
                    .where(ExecutionEvent.execution_id == execution_id, ExecutionEvent.sequence > sequence)
                    .order_by(ExecutionEvent.sequence)
                ).all()
                for item in values:
                    sequence = item.sequence
                    data = {
                        "id": item.id,
                        "sequence": item.sequence,
                        "stream": item.stream,
                        "event_type": item.event_type,
                        "content": item.content,
                        "created_at": str(item.created_at),
                    }
                    yield f"id: {item.sequence}\nevent: log\ndata: {json.dumps(data)}\n\n"
                execution = stream_db.get(AgentExecution, execution_id)
                terminal = not execution or execution.status in {"completed", "failed", "cancelled"}
            if terminal and not values:
                idle_after_terminal += 1
                if idle_after_terminal >= 2:
                    break
            else:
                idle_after_terminal = 0
            yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/v1/executions/{execution_id}/cancel")
def cancel_execution(execution_id: str, db: Session = Depends(get_db)) -> dict:
    execution = db.get(AgentExecution, execution_id)
    if not execution:
        raise HTTPException(404, "execution not found")
    if execution.status not in {"running", "cancel_requested"}:
        raise HTTPException(409, "execution is no longer active")
    execution.cancel_requested_at = execution.cancel_requested_at or utcnow()
    execution.status = "cancel_requested"
    task = db.get(ResearchTask, execution.task_id)
    if task:
        add_event(db, task.run_id, "execution_cancel_requested", f"Cancellation requested for {task.role} task.")
    db.commit()
    return {"id": execution.id, "status": execution.status}


@app.post("/api/v1/tasks/{task_id}/retry")
def retry_task(task_id: str, db: Session = Depends(get_db)) -> dict:
    task = db.get(ResearchTask, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    latest_execution = db.scalar(
        select(AgentExecution)
        .where(AgentExecution.task_id == task.id)
        .order_by(AgentExecution.started_at.desc())
    )
    if task.status not in {"failed", "cancelled"} and not (
        task.status in {"leased", "running"}
        and latest_execution
        and latest_execution.status == "cancel_requested"
    ):
        raise HTTPException(409, "only failed, cancelled, or cancellation-requested tasks can be retried")
    if latest_execution and latest_execution.status == "cancel_requested":
        latest_execution.status = "cancelled"
        latest_execution.completed_at = utcnow()
        latest_execution.diagnostic = "Superseded by a user-requested retry."
    task.status = "queued"
    run = db.get(ResearchRun, task.run_id)
    task.assigned_provider = run.provider_mode if run else None
    task.leased_by = None
    task.lease_expires_at = None
    task.heartbeat_at = None
    task.available_after = None
    task.completed_at = None
    task.max_attempts = max(task.max_attempts, task.attempts + 1)
    if run:
        run.status = "running"
        run.completed_at = None
        run.stop_reason = None
    add_event(db, task.run_id, "task_retried", f"Manual retry queued for {task.role}: {task.objective}")
    db.commit()
    return serialize_task(task, db)


@app.get("/api/v1/projects/{project_id}/graph")
def get_graph(project_id: str, db: Session = Depends(get_db)) -> dict:
    return graph_payload(db, project_id)


@app.get("/api/v1/projects/{project_id}/course")
def get_course(project_id: str, db: Session = Depends(get_db)) -> dict:
    course = db.scalar(
        select(CourseVersion).where(CourseVersion.project_id == project_id).order_by(CourseVersion.version.desc())
    )
    return {"version": course.version, "markdown": course.markdown} if course else {"version": None, "markdown": None}


@app.get("/api/v1/projects/{project_id}/draft")
def get_draft(project_id: str, db: Session = Depends(get_db)) -> dict:
    if not db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    return build_draft(db, project_id)


@app.post("/api/v1/projects/{project_id}/course/regenerate")
def regenerate_course(project_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.scalar(select(ResearchRun).where(ResearchRun.project_id == project_id).order_by(ResearchRun.created_at.desc()))
    if not run:
        raise HTTPException(404, "no research run found")
    course = _assemble_course(db, run)
    db.commit()
    return {"version": course.version, "markdown": course.markdown}


@app.get("/api/v1/projects/{project_id}/export.md", response_class=PlainTextResponse)
def export_course(project_id: str, db: Session = Depends(get_db)) -> str:
    course = db.scalar(
        select(CourseVersion).where(CourseVersion.project_id == project_id).order_by(CourseVersion.version.desc())
    )
    if not course:
        raise HTTPException(404, "no course generated")
    return course.markdown


@app.post("/api/v1/projects/{project_id}/sources", status_code=201)
def add_source(project_id: str, payload: ManualSourceCreate, db: Session = Depends(get_db)) -> dict:
    if not db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    url = str(payload.url)
    source = db.scalar(select(Source).where(Source.project_id == project_id, Source.url == url))
    if not source:
        source = Source(project_id=project_id, url=url, title=payload.title, status="discovered")
        db.add(source)
    run = db.scalar(
        select(ResearchRun)
        .where(
            ResearchRun.project_id == project_id,
            ResearchRun.status.in_(["queued", "running", "reviewing"]),
        )
        .order_by(ResearchRun.created_at.desc())
    )
    if run:
        source.run_id = run.id
        db.flush()
        add_crawl_targets(
            db,
            ensure_crawl_job(db, run),
            [{"url": url, "title": payload.title, "query": "manual source", "relevance_reason": "Added by the user"}],
        )
    source.trust_level = "trusted" if payload.trusted else source.trust_level
    db.commit()
    db.refresh(source)
    return {"id": source.id, "url": source.url, "trust_level": source.trust_level}


@app.post("/api/v1/projects/{project_id}/source-discovery", status_code=202)
async def start_source_discovery(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    run = db.scalar(
        select(ResearchRun).where(
            ResearchRun.project_id == project_id,
            ResearchRun.status.in_(["queued", "running", "reviewing"]),
        ).order_by(ResearchRun.created_at.desc())
    ) or create_run(db, project)
    job = ensure_crawl_job(db, run)
    discovered_sources = db.scalars(
        select(Source).where(Source.project_id == project_id, Source.status == "discovered")
    ).all()
    add_crawl_targets(
        db,
        job,
        [
            {
                "url": source.url,
                "title": source.title,
                "query": project.topic,
                "relevance_reason": "Existing source in the project inbox",
            }
            for source in discovered_sources
        ],
    )
    if job.status in {"paused", "cancelled"}:
        job.status = "queued"
        job.completed_at = None
        job.error = None
        db.commit()
    planning = db.scalar(
        select(ResearchTask).where(ResearchTask.run_id == run.id, ResearchTask.role == "planning")
    )
    if planning:
        context = json.loads(planning.context_json or "{}")
        context.pop("discovery_complete", None)
        planning.context_json = json.dumps(context, ensure_ascii=False)
        db.commit()
    await bootstrap_queued_runs(db)
    db.refresh(job)
    return {"run_id": run.id, "crawl_job_id": job.id, "status": job.status, "max_pages": job.max_pages}


def crawl_dict(job: CrawlJob, db: Session, page: int = 1, page_size: int = 50) -> dict:
    targets = db.scalars(
        select(CrawlTarget)
        .where(CrawlTarget.job_id == job.id)
        .order_by(CrawlTarget.discovered_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "id": job.id,
        "project_id": job.project_id,
        "run_id": job.run_id,
        "status": job.status,
        "max_pages": job.max_pages,
        "discovered_count": job.discovered_count,
        "processed_count": job.processed_count,
        "fetched_count": job.fetched_count,
        "failed_count": job.failed_count,
        "skipped_count": job.skipped_count,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "deadline_at": job.deadline_at,
        "error": job.error,
        "page": page,
        "page_size": page_size,
        "targets": [
            {
                "id": target.id,
                "source_id": target.source_id,
                "url": target.url,
                "canonical_url": target.canonical_url,
                "title": target.title,
                "depth": target.depth,
                "domain": target.domain,
                "status": target.status,
                "relevance_score": target.relevance_score,
                "error": target.error,
                "discovered_at": target.discovered_at,
                "fetched_at": target.fetched_at,
            }
            for target in targets
        ],
    }


@app.get("/api/v1/research-runs/{run_id}/crawl")
def get_crawl(
    run_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    job = db.scalar(select(CrawlJob).where(CrawlJob.run_id == run_id))
    if not job:
        raise HTTPException(404, "crawl job not found")
    return crawl_dict(job, db, page, page_size)


@app.post("/api/v1/crawl-jobs/{job_id}/{action}")
def control_crawl(job_id: str, action: str, db: Session = Depends(get_db)) -> dict:
    if action not in {"pause", "resume", "cancel"}:
        raise HTTPException(404, "unknown crawler action")
    job = db.get(CrawlJob, job_id)
    if not job:
        raise HTTPException(404, "crawl job not found")
    if action == "pause":
        job.status = "paused"
    elif action == "resume":
        job.status = "queued"
        job.completed_at = None
        job.error = None
    else:
        job.status = "cancelled"
        job.completed_at = utcnow()
    add_event(db, job.run_id, f"crawl_{action}", f"Crawler {action} requested by user.")
    db.commit()
    return {"id": job.id, "status": job.status}


@app.get("/api/v1/sources/{source_id}")
def get_source_detail(source_id: str, db: Session = Depends(get_db)) -> dict:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(404, "source not found")
    snapshot = db.scalar(
        select(SourceSnapshot).where(SourceSnapshot.source_id == source.id).order_by(SourceSnapshot.fetched_at.desc())
    )
    chunks = db.scalars(
        select(SourceChunk).where(SourceChunk.source_id == source.id).order_by(SourceChunk.chunk_index).limit(50)
    ).all()
    return {
        "id": source.id,
        "url": source.url,
        "title": source.title,
        "status": source.status,
        "trust_level": source.trust_level,
        "content_type": snapshot.content_type if snapshot else None,
        "http_status": snapshot.http_status if snapshot else None,
        "extraction_error": snapshot.extraction_error if snapshot else None,
        "content": snapshot.content if snapshot else "",
        "chunks": [{"index": item.chunk_index, "content": item.content} for item in chunks],
    }


@app.get("/api/v1/projects/{project_id}/review-items")
def list_review_items(
    project_id: str,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    conditions = [ReviewItem.project_id == project_id]
    conditions.append(ReviewItem.status == (status or "open"))
    total = db.scalar(select(func.count()).select_from(ReviewItem).where(*conditions)) or 0
    items = db.scalars(
        select(ReviewItem)
        .where(*conditions)
        .order_by(ReviewItem.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"items": [review_detail(item, db) for item in items], "total": total, "page": page, "page_size": page_size}


@app.get("/api/v1/review-items/{review_id}")
def get_review_item(review_id: str, db: Session = Depends(get_db)) -> dict:
    item = db.get(ReviewItem, review_id)
    if not item:
        raise HTTPException(404, "review item not found")
    return review_detail(item, db)


@app.patch("/api/v1/review-items/{review_id}")
def decide_review(review_id: str, payload: ReviewDecision, db: Session = Depends(get_db)) -> dict:
    item = db.get(ReviewItem, review_id)
    if not item:
        raise HTTPException(404, "review item not found")
    item.decision = payload.decision
    item.status = "resolved" if payload.decision in {"reject", "accept_as_user_authored", "resolved"} else "open"
    if payload.note:
        item.message = f"{item.message}\n\nDecision note: {payload.note}"
    if payload.decision == "accept_as_user_authored" and item.claim_id:
        from .models import Claim

        claim = db.get(Claim, item.claim_id)
        if claim:
            claim.provenance = "user_authored"
            claim.status = "user_accepted"
    if payload.decision == "research_further" and item.run_id:
        run = db.get(ResearchRun, item.run_id)
        submission = db.get(Submission, item.submission_id) if item.submission_id else None
        parent = db.get(ResearchTask, submission.task_id) if submission else None
        depth = (parent.depth + 1) if parent else 1
        if not run or depth > run.followup_depth_limit or run.tasks_created >= run.task_budget:
            raise HTTPException(409, "follow-up budget or depth limit reached")
        task = create_task(
            db,
            run,
            "followup",
            f"Resolve review issue: {item.message[:1000]}",
            depth=depth,
            parent_task_id=parent.id if parent else None,
            excluded_provider=parent.assigned_provider if parent else None,
        )
        if not task:
            raise HTTPException(409, "follow-up task could not be created")
        run.status = "running"
        run.completed_at = None
        item.status = "resolved"
    if item.claim_id:
        related = db.scalars(
            select(ReviewItem).where(ReviewItem.claim_id == item.claim_id, ReviewItem.status == "open")
        ).all()
        for sibling in related:
            sibling.status = "resolved"
            sibling.decision = payload.decision
    db.commit()
    return {"id": item.id, "status": item.status, "decision": item.decision}


@app.get("/api/v1/agents")
def list_agents(db: Session = Depends(get_db)) -> dict:
    agents = db.scalars(select(AgentInstallation).order_by(AgentInstallation.provider)).all()
    runners = db.scalars(select(RunnerRegistration).order_by(RunnerRegistration.last_heartbeat_at.desc())).all()
    return {
        "agents": [
            {
                "provider": item.provider,
                "status": item.status,
                "version": item.version,
                "mode": item.mode,
                "diagnostic": item.diagnostic,
                "last_seen_at": item.last_seen_at,
            }
            for item in agents
        ],
        "runners": [
            {
                "id": item.id,
                "hostname": item.hostname,
                "status": item.status,
                "last_heartbeat_at": item.last_heartbeat_at,
            }
            for item in runners
        ],
    }


runner_auth = [Depends(require_local_token)]


@app.post("/api/v1/runner/register", dependencies=runner_auth)
def register_runner(payload: RunnerRegister, db: Session = Depends(get_db)) -> dict:
    runner = db.get(RunnerRegistration, payload.runner_id)
    if not runner:
        runner = RunnerRegistration(id=payload.runner_id, hostname=payload.hostname)
        db.add(runner)
    runner.status = "available"
    runner.last_heartbeat_at = utcnow()
    runner.capabilities_json = json.dumps(payload.providers)
    for value in payload.providers:
        provider = value.get("provider")
        if not provider:
            continue
        agent = db.get(AgentInstallation, provider) or AgentInstallation(provider=provider)
        agent.status = value.get("status", "unsupported")
        agent.version = value.get("version")
        agent.mode = value.get("mode", "headless")
        agent.capabilities_json = json.dumps(value.get("capabilities", {}))
        agent.diagnostic = value.get("diagnostic")
        agent.last_seen_at = utcnow()
        db.add(agent)
    db.commit()
    return {"runner_id": runner.id, "status": runner.status}


@app.post("/api/v1/runner/heartbeat", dependencies=runner_auth)
def runner_heartbeat(payload: RunnerHeartbeat, db: Session = Depends(get_db)) -> dict:
    runner = db.get(RunnerRegistration, payload.runner_id)
    if not runner:
        raise HTTPException(404, "runner not registered")
    runner.last_heartbeat_at = utcnow()
    runner.status = "busy" if payload.busy_providers else "available"
    db.commit()
    return {"status": runner.status}


@app.post("/api/v1/runner/tasks/claim", dependencies=runner_auth)
def runner_claim(payload: TaskClaimRequest, db: Session = Depends(get_db)) -> dict:
    value = claim_task(db, payload.runner_id, payload.provider, payload.cli_version)
    if not value:
        return {"task": None}
    task, execution = value
    return {"task": serialize_task(task, db), "context": build_task_context(db, task), "execution_id": execution.id}


@app.post("/api/v1/runner/kiro-launches/claim", dependencies=runner_auth)
def runner_claim_kiro_launch(payload: KiroLaunchClaim, db: Session = Depends(get_db)) -> dict:
    if not db.get(RunnerRegistration, payload.runner_id):
        raise HTTPException(404, "runner not registered")
    task = claim_kiro_launch(db)
    return {"task": serialize_task(task, db) if task else None}


@app.post("/api/v1/runner/kiro-launches/{task_id}/complete", dependencies=runner_auth)
def runner_complete_kiro_launch(task_id: str, payload: TaskRelease, db: Session = Depends(get_db)) -> dict:
    task = db.get(ResearchTask, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    error = None if payload.reason == "opened" else payload.reason
    report_kiro_launch(db, task, error)
    return {"task_id": task.id, "opened": error is None}


@app.post("/api/v1/runner/tasks/{task_id}/heartbeat", dependencies=runner_auth)
def runner_task_heartbeat(task_id: str, payload: TaskHeartbeat, db: Session = Depends(get_db)) -> dict:
    task = db.get(ResearchTask, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    try:
        cancel_requested = heartbeat_task(db, task, payload.runner_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": task.status, "lease_expires_at": task.lease_expires_at, "cancel_requested": cancel_requested}


@app.post("/api/v1/runner/executions/{execution_id}/events", dependencies=runner_auth)
def append_execution_events(execution_id: str, payload: ExecutionEventBatch, db: Session = Depends(get_db)) -> dict:
    execution = db.get(AgentExecution, execution_id)
    if not execution:
        raise HTTPException(404, "execution not found")
    if execution.runner_id != payload.runner_id:
        raise HTTPException(409, "execution belongs to another runner")
    current_sequence = db.scalar(
        select(func.max(ExecutionEvent.sequence)).where(ExecutionEvent.execution_id == execution.id)
    ) or 0
    accepted = 0
    for incoming in payload.events:
        sanitized = _sanitize_execution_content(incoming.content, incoming.event_type)
        encoded_size = len(sanitized.encode("utf-8"))
        remaining = settings.max_execution_log_bytes - execution.output_bytes
        if remaining <= 0:
            break
        if encoded_size > remaining:
            sanitized = sanitized.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
            encoded_size = len(sanitized.encode("utf-8"))
        current_sequence += 1
        db.add(
            ExecutionEvent(
                execution_id=execution.id,
                sequence=current_sequence,
                stream=incoming.stream,
                event_type=incoming.event_type,
                content=sanitized,
            )
        )
        execution.output_bytes += encoded_size
        accepted += 1
    db.commit()
    return {"accepted": accepted, "output_bytes": execution.output_bytes, "truncated": accepted < len(payload.events)}


@app.post("/api/v1/runner/tasks/{task_id}/release", dependencies=runner_auth)
def runner_task_release(task_id: str, payload: TaskRelease, db: Session = Depends(get_db)) -> dict:
    task = db.get(ResearchTask, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    if task.leased_by != payload.runner_id:
        raise HTTPException(409, "task is leased by another runner")
    task.status = "queued"
    task.assigned_provider = None
    task.leased_by = None
    task.lease_expires_at = None
    task.heartbeat_at = None
    add_event(db, task.run_id, "task_released", f"{payload.provider} released task: {payload.reason[:200]}")
    db.commit()
    return {"status": task.status, "reason": payload.reason}


@app.post("/api/v1/runner/tasks/{task_id}/submit", dependencies=runner_auth)
async def runner_submit(task_id: str, payload: TaskSubmitRequest, db: Session = Depends(get_db)) -> dict:
    task = db.get(ResearchTask, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    if task.leased_by != payload.runner_id:
        raise HTTPException(409, "task is leased by another runner")
    try:
        submission = await accept_submission(
            db,
            task,
            payload.provider,
            payload.cli_version,
            payload.prompt_version,
            payload.result,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"submission_id": submission.id, "validation_status": submission.validation_status}


@app.post("/api/v1/runner/tasks/{task_id}/fail", dependencies=runner_auth)
def runner_fail(task_id: str, payload: TaskFailRequest, db: Session = Depends(get_db)) -> dict:
    task = db.get(ResearchTask, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    try:
        fail_task(db, task, payload.runner_id, payload.diagnostic, payload.exit_code)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.refresh(task)
    if "execution cancelled" in payload.diagnostic.casefold() and task.status != "cancelled":
        task.status = "cancelled"
        task.completed_at = utcnow()
        task.assigned_provider = payload.provider
        db.commit()
    return {"status": task.status}
