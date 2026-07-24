from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import time
import traceback

from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .database import SessionLocal, init_db
from .models import AgentExecution, AgentInstallation, AgentProfile, CourseVersion, DocumentationRun, ResearchRun, ResearchTask, RunnerRegistration, utcnow
from .services.events import add_event
from .services.crawler import get_research_settings
from .services.langgraph_runtime import run_documentation_autoresearch, run_research_execution
from .services.orchestration import _assemble_course, accept_submission, build_task_context, claim_task, fail_task, heartbeat_task


settings = get_settings()
RUNNER_ID = f"langgraph-{socket.gethostname()}-{os.getpid()}"


def _register_once() -> None:
    with SessionLocal() as db:
        if settings.database_url.startswith("sqlite"):
            db.connection().exec_driver_sql("PRAGMA busy_timeout=1500")
        runner = db.get(RunnerRegistration, RUNNER_ID) or RunnerRegistration(id=RUNNER_ID, hostname=socket.gethostname())
        runner.status = "available" if settings.azure_ready else "authentication_required"
        runner.last_heartbeat_at = utcnow()
        configured = get_research_settings(db)
        runner.capabilities_json = json.dumps({"provider": "inhouse_azure", "langgraph": True, "concurrency": configured.inhouse_agent_concurrency})
        db.add(runner)
        agent = db.get(AgentInstallation, "inhouse_azure") or AgentInstallation(provider="inhouse_azure")
        agent.status = "available" if settings.azure_ready else "authentication_required"
        agent.version = "langgraph-local-v1"
        agent.mode = "headless"
        agent.capabilities_json = json.dumps({"checkpointing": True, "safe_tools": True, "documentation_autoresearch": True, "single_model_policy": True, "deployments": [settings.azure_reasoning_deployment]})
        agent.diagnostic = None if settings.azure_ready else "Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in the local environment."
        agent.last_seen_at = utcnow()
        db.add(agent)
        profiles = {
            "planner": ("planning", settings.azure_reasoning_deployment, ["search_web", "search_stored_sources", "read_source_chunks", "get_graph_context", "propose_followup"]),
            "researcher": ("research", settings.azure_reasoning_deployment, ["search_web", "search_stored_sources", "read_source_chunks", "get_claim_context", "get_graph_context", "get_course_page", "propose_followup"]),
            "reviewer": ("review", settings.azure_reasoning_deployment, ["search_stored_sources", "read_source_chunks", "get_claim_context", "get_graph_context", "get_course_page", "propose_followup"]),
            "gap-fixer": ("followup", settings.azure_reasoning_deployment, ["search_web", "search_stored_sources", "read_source_chunks", "get_claim_context", "get_graph_context", "get_course_page", "propose_followup"]),
            "course-architect": ("course_architect", settings.azure_reasoning_deployment, ["get_claim_context", "get_graph_context", "get_course_page"]),
            "page-writer": ("page_writer", settings.azure_reasoning_deployment, ["search_stored_sources", "read_source_chunks", "get_claim_context", "get_graph_context", "get_course_page"]),
            "documentation-evaluator": ("documentation_evaluator", settings.azure_reasoning_deployment, ["get_course_page", "get_claim_context"]),
        }
        for profile_id, (role, model, tools) in profiles.items():
            profile = db.get(AgentProfile, profile_id) or AgentProfile(id=profile_id, role=role, model=model)
            profile.role = role
            profile.model = model
            profile.tools_json = json.dumps(tools)
            profile.max_tool_rounds = configured.inhouse_tool_rounds
            profile.enabled = True
            db.add(profile)
        db.commit()


def register() -> bool:
    """Refresh worker capabilities without exiting on a transient SQLite writer lock."""
    for attempt in range(5):
        try:
            _register_once()
            return True
        except OperationalError as exc:
            if "locked" not in str(exc).casefold() or attempt == 4:
                return False
            time.sleep(0.1 * (2**attempt))
    return False


def recover_interrupted_documentation_runs() -> None:
    """A fresh process cannot own a prior process's active documentation graph."""
    for attempt in range(5):
        try:
            with SessionLocal() as db:
                if settings.database_url.startswith("sqlite"):
                    db.connection().exec_driver_sql("PRAGMA busy_timeout=1500")
                runs = db.scalars(select(DocumentationRun).where(DocumentationRun.status == "running")).all()
                for run in runs:
                    run.status = "queued"
                    run.error = "The local worker restarted; resuming this checkpointed documentation run."
                if runs:
                    db.commit()
                return
        except OperationalError as exc:
            if "locked" not in str(exc).casefold() or attempt == 4:
                raise
            time.sleep(0.1 * (2**attempt))


def enforce_budgets() -> None:
    with SessionLocal() as db:
        runs = db.scalars(
            select(ResearchRun).where(
                ResearchRun.status.in_(["queued", "running", "reviewing"]),
                or_(
                    ResearchRun.tokens_used >= ResearchRun.token_budget,
                    ResearchRun.cost_used_usd >= ResearchRun.cost_budget_usd,
                ),
            )
        ).all()
        for run in runs:
            tasks = db.scalars(select(ResearchTask).where(ResearchTask.run_id == run.id)).all()
            for task in tasks:
                if task.status == "queued":
                    task.status = "cancelled"
                    task.completed_at = utcnow()
                elif task.status in {"leased", "running"}:
                    execution = db.scalar(
                        select(AgentExecution)
                        .where(AgentExecution.task_id == task.id, AgentExecution.status == "running")
                        .order_by(AgentExecution.started_at.desc())
                    )
                    if execution and not execution.cancel_requested_at:
                        execution.cancel_requested_at = utcnow()
                        execution.status = "cancel_requested"
            active = any(task.status in {"leased", "running"} for task in tasks)
            if not active:
                existing_course = db.scalar(select(CourseVersion).where(CourseVersion.run_id == run.id))
                if not existing_course:
                    _assemble_course(db, run)
                run.status = "completed"
                run.completed_at = utcnow()
                run.stop_reason = "Azure token or estimated-cost budget reached; partial verified notes were assembled."
                add_event(db, run.id, "budget_exhausted", run.stop_reason)
        if runs:
            db.commit()


def release_worker_leases() -> None:
    """Make an interrupted container restart recover immediately, not after lease expiry."""
    with SessionLocal() as db:
        tasks = db.scalars(
            select(ResearchTask).where(
                ResearchTask.leased_by == RUNNER_ID,
                ResearchTask.status.in_(["leased", "running"]),
            )
        ).all()
        for task in tasks:
            execution = db.scalar(
                select(AgentExecution)
                .where(AgentExecution.task_id == task.id, AgentExecution.status.in_(["running", "cancel_requested"]))
                .order_by(AgentExecution.started_at.desc())
            )
            if execution:
                execution.status = "failed"
                execution.completed_at = utcnow()
                execution.diagnostic = "Local LangGraph worker stopped; task was safely requeued."
            run = db.get(ResearchRun, task.run_id)
            task.status = "queued"
            task.assigned_provider = run.provider_mode if run else "inhouse_azure"
            task.leased_by = None
            task.lease_expires_at = None
            task.heartbeat_at = None
            task.available_after = None
            task.max_attempts = max(task.max_attempts, task.attempts + 1)
            add_event(db, task.run_id, "task_requeued", "LangGraph worker stopped; task requeued from its checkpoint.")
        documentation_runs = db.scalars(
            select(DocumentationRun).where(DocumentationRun.status == "running")
        ).all()
        for run in documentation_runs:
            run.status = "queued"
            run.error = "Local LangGraph worker stopped; the checkpointed documentation run will resume."
        runner = db.get(RunnerRegistration, RUNNER_ID)
        if runner:
            runner.status = "offline"
            runner.last_heartbeat_at = utcnow()
        db.commit()


async def heartbeat(task_id: str, stop: asyncio.Event, cancel_requested: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            with SessionLocal() as db:
                task = db.get(ResearchTask, task_id)
                if task:
                    if heartbeat_task(db, task, RUNNER_ID):
                        cancel_requested.set()
                        return
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            continue


async def execute(task_id: str, execution_id: str) -> None:
    stop = asyncio.Event()
    cancel_requested = asyncio.Event()
    heartbeat_job = asyncio.create_task(heartbeat(task_id, stop, cancel_requested))
    try:
        with SessionLocal() as db:
            task = db.get(ResearchTask, task_id)
            context = build_task_context(db, task)
            execution = db.get(AgentExecution, execution_id)
            execution.command_json = json.dumps({"runtime": "langgraph", "sandbox": "tool-allowlist", "prompt_transport": "memory", "deployment": settings.azure_reasoning_deployment, "single_model_policy": True})
            db.commit()
        graph_job = asyncio.create_task(run_research_execution(task_id, execution_id, context))
        cancellation_job = asyncio.create_task(cancel_requested.wait())
        done, _ = await asyncio.wait(
            {graph_job, cancellation_job},
            timeout=settings.inhouse_run_deadline_minutes * 60,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if graph_job in done:
            cancellation_job.cancel()
            result = await graph_job
        elif cancellation_job in done:
            graph_job.cancel()
            try:
                await graph_job
            except BaseException:
                pass
            raise RuntimeError("execution cancelled by user")
        else:
            graph_job.cancel()
            cancellation_job.cancel()
            raise TimeoutError(f"execution exceeded the {settings.inhouse_run_deadline_minutes}-minute deadline")
        with SessionLocal() as db:
            task = db.get(ResearchTask, task_id)
            await accept_submission(db, task, "inhouse_azure", "langgraph-local-v1", "langgraph-v1", result)
    except Exception as exc:
        with SessionLocal() as db:
            task = db.get(ResearchTask, task_id)
            if task and task.leased_by == RUNNER_ID:
                fail_task(db, task, RUNNER_ID, f"{type(exc).__name__}: {str(exc)[:3200]}", 1)
            execution = db.get(AgentExecution, execution_id)
            if execution:
                execution.diagnostic = f"{type(exc).__name__}: {str(exc)[:3200]}"
                db.commit()
        traceback.print_exc()
    finally:
        stop.set()
        await heartbeat_job


async def execute_documentation(run_id: str) -> None:
    try:
        await run_documentation_autoresearch(run_id)
    except Exception as exc:
        with SessionLocal() as db:
            run = db.get(DocumentationRun, run_id)
            if run:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {str(exc)[:3200]}"
                run.completed_at = utcnow()
                db.commit()
        traceback.print_exc()


async def main() -> None:
    init_db()
    recover_interrupted_documentation_runs()
    register()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            pass
    active: dict[str, asyncio.Task] = {}
    documentation_active: dict[str, asyncio.Task] = {}
    while not stop.is_set():
        active = {key: value for key, value in active.items() if not value.done()}
        documentation_active = {key: value for key, value in documentation_active.items() if not value.done()}
        try:
            register()
            enforce_budgets()
        except OperationalError:
            # Another local service may briefly own SQLite's writer lock.
            # Keep the process alive and retry the scheduling pass.
            await asyncio.sleep(1)
            continue
        if settings.azure_ready:
            try:
                with SessionLocal() as db:
                    desired_concurrency = get_research_settings(db).inhouse_agent_concurrency
            except OperationalError:
                await asyncio.sleep(1)
                continue
            while len(active) < desired_concurrency:
                try:
                    with SessionLocal() as db:
                        claimed = claim_task(db, RUNNER_ID, "inhouse_azure", "langgraph-local-v1")
                        if not claimed:
                            break
                        task, execution = claimed
                except OperationalError:
                    break
                active[execution.id] = asyncio.create_task(execute(task.id, execution.id))
            if not documentation_active:
                try:
                    with SessionLocal() as db:
                        doc_run = db.scalar(select(DocumentationRun).where(DocumentationRun.status == "queued").order_by(DocumentationRun.created_at))
                        if doc_run:
                            documentation_active[doc_run.id] = asyncio.create_task(execute_documentation(doc_run.id))
                except OperationalError:
                    pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except TimeoutError:
            continue
    for job in [*active.values(), *documentation_active.values()]:
        job.cancel()
    await asyncio.gather(*active.values(), *documentation_active.values(), return_exceptions=True)
    release_worker_leases()


if __name__ == "__main__":
    asyncio.run(main())
