from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    AgentExecution,
    AgentInstallation,
    Claim,
    Concept,
    CourseExpansionRequest,
    CourseVersion,
    CrawlJob,
    Evidence,
    Project,
    Relationship,
    ResearchRun,
    ResearchTask,
    ReviewItem,
    Source,
    SourceSnapshot,
    Submission,
    utcnow,
)
from ..schemas import AgentReviewResult, AgentTaskResult, RELATION_TYPES
from .embeddings import cosine_similarity, embed_text
from .crawler import ensure_crawl_job, get_research_settings, register_agent_candidates
from .events import add_event
from .evidence import fetch_document, verify_quote


TERMINAL_TASK_STATES = {"completed", "failed", "cancelled"}
ACTIVE_TASK_STATES = {"queued", "leased", "running"}


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def create_task(
    db: Session,
    run: ResearchRun,
    role: str,
    objective: str,
    *,
    depth: int = 0,
    parent_task_id: str | None = None,
    excluded_provider: str | None = None,
    assigned_provider: str | None = None,
    context: dict | None = None,
) -> ResearchTask | None:
    if run.tasks_created >= run.task_budget:
        return None
    key = f"{run.id}:{role}:{normalize_name(objective)[:100]}:{depth}"
    existing = db.scalar(select(ResearchTask).where(ResearchTask.idempotency_key == key))
    if existing:
        return existing
    task = ResearchTask(
        run_id=run.id,
        parent_task_id=parent_task_id,
        role=role,
        objective=objective,
        depth=depth,
        excluded_provider=excluded_provider,
        assigned_provider=(
            assigned_provider
            if assigned_provider is not None
            else (run.provider_mode if run.provider_mode != "inhouse_azure" or get_settings().azure_ready else None)
        ),
        idempotency_key=key,
        context_json=json.dumps(context or {}, ensure_ascii=False),
    )
    db.add(task)
    run.tasks_created += 1
    add_event(db, run.id, "task_created", f"Created {role} task: {objective}", {"role": role})
    return task


def create_run(
    db: Session,
    project: Project,
    task_budget: int | None = None,
    source_budget: int | None = None,
    followup_depth_limit: int = 2,
    provider_mode: str | None = None,
    planning_objective: str | None = None,
    planning_context: dict | None = None,
) -> ResearchRun:
    active = db.scalar(
        select(ResearchRun).where(
            ResearchRun.project_id == project.id,
            ResearchRun.status.in_(["queued", "running", "reviewing"]),
        )
    )
    if active:
        return active
    configured = get_research_settings(db)
    run = ResearchRun(
        project_id=project.id,
        status="queued",
        task_budget=task_budget or configured.default_task_budget,
        source_budget=source_budget or configured.default_source_budget,
        followup_depth_limit=followup_depth_limit,
        provider_mode=provider_mode or configured.provider_mode,
        langgraph_thread_id=None,
        token_budget=configured.azure_token_budget,
        cost_budget_usd=configured.azure_cost_budget_usd,
    )
    db.add(run)
    db.flush()
    run.langgraph_thread_id = f"research:{run.id}"
    create_task(
        db,
        run,
        "planning",
        planning_objective or f"Build a prerequisite-aware research plan for: {project.topic}",
        context=planning_context or {"topic": project.topic, "goal": project.goal, "learner_level": project.learner_level},
    )
    add_event(db, run.id, "run_created", f"Research queued for {project.title}")
    db.commit()
    db.refresh(run)
    ensure_crawl_job(db, run)
    return run


def request_kiro_research(db: Session, project: Project) -> tuple[ResearchRun, ResearchTask]:
    """Reserve the next queued task for a single explicit Kiro UI launch."""
    run = db.scalar(
        select(ResearchRun)
        .where(
            ResearchRun.project_id == project.id,
            ResearchRun.status.in_(["queued", "running", "reviewing"]),
        )
        .order_by(ResearchRun.created_at.desc())
    )
    if not run:
        run = create_run(db, project)
    task = db.scalar(
        select(ResearchTask)
        .where(
            ResearchTask.run_id == run.id,
            ResearchTask.status == "queued",
            or_(ResearchTask.assigned_provider.is_(None), ResearchTask.assigned_provider == "kiro"),
        )
        .order_by(ResearchTask.created_at)
    )
    if not task:
        raise ValueError("no queued task is available for Kiro")
    context = json.loads(task.context_json or "{}")
    context.update({"kiro_launch_requested": True, "kiro_launched_at": None, "kiro_launch_error": None})
    task.context_json = json.dumps(context, ensure_ascii=False)
    task.assigned_provider = "kiro"
    add_event(db, run.id, "kiro_requested", f"Kiro was requested for {task.role}: {task.objective}", {"task_id": task.id})
    db.commit()
    db.refresh(task)
    return run, task


def request_codex_research(db: Session, project: Project) -> tuple[ResearchRun, ResearchTask]:
    """Reserve the next queued task for the local Codex CLI worker."""
    run = db.scalar(
        select(ResearchRun)
        .where(
            ResearchRun.project_id == project.id,
            ResearchRun.status.in_(["queued", "running", "reviewing"]),
        )
        .order_by(ResearchRun.created_at.desc())
    )
    if not run:
        run = create_run(db, project)
    task = db.scalar(
        select(ResearchTask)
        .where(
            ResearchTask.run_id == run.id,
            ResearchTask.status == "queued",
            or_(
                ResearchTask.assigned_provider.is_(None),
                ResearchTask.assigned_provider.in_(["codex", "kiro", "inhouse_azure"]),
            ),
        )
        .order_by(ResearchTask.created_at)
    )
    if not task:
        raise ValueError("no queued task is available for Codex")
    context = json.loads(task.context_json or "{}")
    for key in ("kiro_launch_requested", "kiro_launched_at", "kiro_launch_error"):
        context.pop(key, None)
    task.context_json = json.dumps(context, ensure_ascii=False)
    task.assigned_provider = "codex"
    add_event(db, run.id, "codex_requested", f"Codex was requested for {task.role}: {task.objective}", {"task_id": task.id})
    db.commit()
    db.refresh(task)
    return run, task


def claim_kiro_launch(db: Session) -> ResearchTask | None:
    tasks = db.scalars(
        select(ResearchTask)
        .join(ResearchRun, ResearchRun.id == ResearchTask.run_id)
        .where(
            ResearchTask.status == "queued",
            ResearchTask.assigned_provider == "kiro",
            ResearchRun.status.not_in(["cancelled", "completed", "failed"]),
        )
        .order_by(ResearchTask.created_at)
    ).all()
    for task in tasks:
        context = json.loads(task.context_json or "{}")
        if context.get("kiro_launch_requested") and not context.get("kiro_launched_at"):
            context["kiro_launched_at"] = utcnow().isoformat()
            task.context_json = json.dumps(context, ensure_ascii=False)
            add_event(db, task.run_id, "kiro_launching", "Opening Kiro with the reserved Atlas research task.", {"task_id": task.id})
            db.commit()
            db.refresh(task)
            return task
    return None


def report_kiro_launch(db: Session, task: ResearchTask, error: str | None = None) -> None:
    context = json.loads(task.context_json or "{}")
    context["kiro_launch_error"] = error
    if error:
        context["kiro_launched_at"] = None
        add_event(db, task.run_id, "kiro_launch_failed", f"Kiro could not be opened: {error[:300]}", {"task_id": task.id})
    else:
        add_event(db, task.run_id, "kiro_opened", "Kiro opened and can claim the reserved task through Atlas MCP.", {"task_id": task.id})
    task.context_json = json.dumps(context, ensure_ascii=False)
    db.commit()


def release_expired_leases(db: Session) -> int:
    now = utcnow()
    tasks = db.scalars(
        select(ResearchTask).where(
            ResearchTask.status.in_(["leased", "running"]),
            ResearchTask.lease_expires_at.is_not(None),
            ResearchTask.lease_expires_at < now,
        )
    ).all()
    for task in tasks:
        if task.attempts >= task.max_attempts:
            task.status = "failed"
            task.completed_at = now
            add_event(db, task.run_id, "task_failed", f"Task failed after lease expiry: {task.objective}")
            run = db.get(ResearchRun, task.run_id)
            if task.role == "planning":
                run.status = "failed"
                run.completed_at = now
                run.stop_reason = "Planning task exceeded its retry limit after lease expiry."
            else:
                _maybe_create_review(db, run)
        else:
            task.status = "queued"
            task.leased_by = None
            task.lease_expires_at = None
            task.heartbeat_at = None
            add_event(db, task.run_id, "task_requeued", f"Lease expired; task requeued: {task.objective}")
    if tasks:
        db.commit()
    return len(tasks)


def claim_task(db: Session, runner_id: str, provider: str, cli_version: str | None = None) -> tuple[ResearchTask, AgentExecution] | None:
    release_expired_leases(db)
    now = utcnow()

    def find_candidate(include_same_provider_fallback: bool = False) -> ResearchTask | None:
        conditions = [
            ResearchTask.status == "queued",
            ResearchRun.status.not_in(["cancelled", "completed", "failed"]),
            ResearchRun.tokens_used < ResearchRun.token_budget,
            ResearchRun.cost_used_usd < ResearchRun.cost_budget_usd,
            or_(ResearchTask.assigned_provider.is_(None), ResearchTask.assigned_provider == provider),
            or_(ResearchTask.available_after.is_(None), ResearchTask.available_after <= now),
        ]
        if include_same_provider_fallback:
            conditions.append(ResearchTask.excluded_provider == provider)
        else:
            conditions.append(or_(ResearchTask.excluded_provider.is_(None), ResearchTask.excluded_provider != provider))
        return db.scalar(
            select(ResearchTask)
            .join(ResearchRun, ResearchRun.id == ResearchTask.run_id)
            .where(*conditions)
            .order_by(ResearchTask.created_at)
        )

    task = find_candidate()
    if not task:
        # A provider-specific queue (the local Azure worker by default) must
        # still be able to use a fresh same-provider session for review.
        task = find_candidate(include_same_provider_fallback=True)
    if not task:
        other_headless_available = db.scalar(
            select(func.count())
            .select_from(AgentInstallation)
            .where(
                AgentInstallation.provider != provider,
                AgentInstallation.status == "available",
                AgentInstallation.mode == "headless",
            )
        )
        if not other_headless_available:
            task = find_candidate(include_same_provider_fallback=True)
    if not task:
        return None
    claimed = db.execute(
        update(ResearchTask)
        .where(ResearchTask.id == task.id, ResearchTask.status == "queued")
        .values(
            status="leased",
            assigned_provider=provider,
            leased_by=runner_id,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=get_settings().task_lease_seconds),
            available_after=None,
            attempts=ResearchTask.attempts + 1,
        )
    )
    if claimed.rowcount != 1:
        db.rollback()
        return claim_task(db, runner_id, provider, cli_version)
    db.flush()
    task = db.get(ResearchTask, task.id)
    run = db.get(ResearchRun, task.run_id)
    if run and run.status == "queued":
        run.status = "running"
        run.started_at = now
    execution = AgentExecution(
        task_id=task.id,
        runner_id=runner_id,
        provider=provider,
        status="running",
        command_json=json.dumps({"capability_selected": True, "cli_version": cli_version}),
        last_heartbeat_at=now,
    )
    db.add(execution)
    add_event(db, task.run_id, "task_claimed", f"{provider} claimed {task.role} task", {"task_id": task.id})
    db.commit()
    db.refresh(task)
    db.refresh(execution)
    return task, execution


def heartbeat_task(db: Session, task: ResearchTask, runner_id: str) -> bool:
    if task.leased_by != runner_id or task.status not in {"leased", "running"}:
        raise ValueError("task is not leased by this runner")
    now = utcnow()
    task.status = "running"
    task.heartbeat_at = now
    task.lease_expires_at = now + timedelta(seconds=get_settings().task_lease_seconds)
    execution = db.scalar(
        select(AgentExecution)
        .where(AgentExecution.task_id == task.id, AgentExecution.runner_id == runner_id, AgentExecution.status.in_(["running", "cancel_requested"]))
        .order_by(AgentExecution.started_at.desc())
    )
    if execution:
        execution.last_heartbeat_at = now
    db.commit()
    return bool(execution and execution.cancel_requested_at)


def build_task_context(db: Session, task: ResearchTask) -> dict:
    run = db.get(ResearchRun, task.run_id)
    project = db.get(Project, run.project_id) if run else None
    base = json.loads(task.context_json or "{}")
    base.update(
        {
            "task_id": task.id,
            "role": task.role,
            "objective": task.objective,
            "topic": project.topic if project else "",
            "project": {
                "id": project.id if project else "",
                "title": project.title if project else "",
                "topic": project.topic if project else "",
            },
            "project_goal": project.goal if project else "",
            "learner_level": project.learner_level if project else "",
            "evidence_policy": (
                "Agent prose is not evidence. Return public source URLs and exact quotations. "
                "Clearly separate supported claims, synthesis, and hypotheses."
            ),
            "codex_web_research": get_research_settings(db).codex_web_research,
        }
    )
    if task.role in {"review", "followup"} and run:
        submissions = db.scalars(
            select(Submission)
            .join(ResearchTask, ResearchTask.id == Submission.task_id)
            .where(ResearchTask.run_id == run.id, Submission.kind == "result")
            .order_by(Submission.created_at)
        ).all()
        submission_context = []
        for item in submissions:
            claims = db.scalars(select(Claim).where(Claim.submission_id == item.id)).all()
            submission_context.append(
                {
                    "submission_id": item.id,
                    "provider": item.provider,
                    "payload": json.loads(item.payload_json),
                    "validated_claims": [
                        {"claim_id": claim.id, "text": claim.text, "status": claim.status, "provenance": claim.provenance}
                        for claim in claims
                    ],
                }
            )
        base["submissions_to_review"] = submission_context
    sources = db.scalars(
        select(Source).where(Source.project_id == (project.id if project else ""), Source.status.in_(["discovered", "fetched"]))
    ).all()
    base["source_hints"] = [{"url": source.url, "title": source.title} for source in sources[:50]]
    return base


async def _get_or_fetch_source(db: Session, project_id: str, run_id: str, evidence) -> tuple[Source, SourceSnapshot | None, str | None]:
    url = str(evidence.url)
    source = db.scalar(select(Source).where(Source.project_id == project_id, Source.url == url))
    if not source:
        source = Source(project_id=project_id, run_id=run_id, url=url, title=evidence.title, status="fetching")
        db.add(source)
        db.flush()
        db.commit()
    snapshot = db.scalar(
        select(SourceSnapshot).where(SourceSnapshot.source_id == source.id).order_by(SourceSnapshot.fetched_at.desc())
    )
    if snapshot and snapshot.content:
        return source, snapshot, None
    try:
        document = await fetch_document(url)
        source.url = document.url
        source.status = "fetched"
        source.content_hash = document.content_hash
        snapshot = SourceSnapshot(
            source_id=source.id,
            content=document.content,
            content_type=document.content_type,
            http_status=document.status_code,
        )
        db.add(snapshot)
        db.flush()
        return source, snapshot, None
    except Exception as exc:
        source.status = "failed"
        snapshot = SourceSnapshot(source_id=source.id, extraction_error=str(exc)[:1000])
        db.add(snapshot)
        db.flush()
        return source, snapshot, str(exc)


async def persist_research_result(
    db: Session,
    task: ResearchTask,
    submission: Submission,
    result: AgentTaskResult,
) -> list[Claim]:
    run = db.get(ResearchRun, task.run_id)
    project = db.get(Project, run.project_id)
    register_agent_candidates(db, run, result.source_candidates)
    persisted_claims: list[Claim] = []
    all_supported = bool(result.claims)
    for claim_input in result.claims:
        claim = Claim(
            project_id=project.id,
            task_id=task.id,
            submission_id=submission.id,
            text=claim_input.text,
            provenance="llm_hypothesis",
            status="unsupported",
        )
        db.add(claim)
        db.flush()
        db.commit()
        verified_domains: set[str] = set()
        trusted = False
        for evidence_input in claim_input.evidence:
            existing_source = db.scalar(
                select(Source).where(Source.project_id == project.id, Source.url == str(evidence_input.url))
            )
            source_count = db.scalar(
                select(func.count()).select_from(Source).where(Source.run_id == run.id)
            ) or 0
            if not existing_source and source_count >= run.source_budget:
                db.add(
                    Evidence(
                        claim_id=claim.id,
                        source_id=None,
                        quote=evidence_input.quote,
                        locator=evidence_input.locator,
                        verified=False,
                        error="research run source budget reached",
                    )
                )
                db.commit()
                continue
            source, snapshot, fetch_error = await _get_or_fetch_source(db, project.id, run.id, evidence_input)
            verified = bool(snapshot and snapshot.content and verify_quote(snapshot.content, evidence_input.quote))
            error = fetch_error or (None if verified else "quotation was not found in the fetched source")
            db.add(
                Evidence(
                    claim_id=claim.id,
                    source_id=source.id,
                    quote=evidence_input.quote,
                    locator=evidence_input.locator,
                    verified=verified,
                    error=error,
                )
            )
            if verified:
                hostname = urlparse(source.url).hostname
                if hostname:
                    verified_domains.add(hostname.casefold().removeprefix("www."))
                trusted = trusted or source.trust_level == "trusted"
            db.commit()
        if trusted or len(verified_domains) >= 2:
            claim.provenance = "source_supported"
            claim.status = "supported"
        elif verified_domains:
            # One verified quotation is still source-supported evidence. Keep
            # the single-source status so it receives elevated review priority
            # without misclassifying the claim as uncited LLM synthesis.
            claim.provenance = "source_supported"
            if get_research_settings(db).auto_accept_verified_single_source:
                claim.status = "supported"
            else:
                claim.status = "single_source"
                all_supported = False
                db.add(
                    ReviewItem(
                        project_id=project.id,
                        run_id=run.id,
                        submission_id=submission.id,
                        claim_id=claim.id,
                        category="single_source",
                        message="Claim has only one independently verified source.",
                    )
                )
        else:
            all_supported = False
            if not get_research_settings(db).auto_resolve_evidence_exceptions:
                db.add(
                    ReviewItem(
                        project_id=project.id,
                        run_id=run.id,
                        submission_id=submission.id,
                        claim_id=claim.id,
                        category="unsupported_claim",
                        message="No submitted quotation could be verified.",
                    )
                )
        persisted_claims.append(claim)
    submission.validation_status = "supported" if all_supported else ("mixed" if persisted_claims else "unsupported")
    _upsert_graph(db, project, run, submission, result, persisted_claims)
    return persisted_claims


def _upsert_graph(
    db: Session,
    project: Project,
    run: ResearchRun,
    submission: Submission,
    result: AgentTaskResult,
    claims: list[Claim],
) -> None:
    supported_indexes = {
        index for index, claim in enumerate(claims)
        if claim.status in {"supported", "single_source", "reviewed_supported"}
    }
    concept_map: dict[str, Concept] = {}
    for proposal in result.concepts:
        matched_indexes = supported_indexes.intersection(proposal.claim_indexes)
        if not matched_indexes:
            continue
        supporting_index = min(matched_indexes)
        normalized = normalize_name(proposal.name)
        concept = db.scalar(
            select(Concept).where(Concept.project_id == project.id, Concept.normalized_name == normalized)
        )
        vector = embed_text(f"{proposal.name}: {proposal.summary}") if not concept else None
        if not concept and vector:
            candidates = db.scalars(
                select(Concept).where(Concept.project_id == project.id, Concept.embedding_json.is_not(None))
            ).all()
            scored = [
                (cosine_similarity(vector, json.loads(candidate.embedding_json)), candidate)
                for candidate in candidates
                if candidate.embedding_json
            ]
            if scored:
                score, candidate = max(scored, key=lambda item: item[0])
                if score >= 0.90:
                    concept = candidate
        if not concept:
            concept = Concept(
                project_id=project.id,
                name=proposal.name,
                normalized_name=normalized,
                concept_type=proposal.concept_type,
                summary=proposal.summary,
                provenance="source_supported",
                status="supported",
                supporting_claim_id=claims[supporting_index].id,
                embedding_json=json.dumps(vector) if vector else None,
            )
            db.add(concept)
            db.flush()
        elif len(proposal.summary) > len(concept.summary):
            concept.summary = proposal.summary
        if concept.status != "supported":
            concept.status = "supported"
            concept.supporting_claim_id = claims[supporting_index].id
        concept_map[normalized] = concept
    for proposal in result.relationships:
        if proposal.relation_type not in RELATION_TYPES:
            if not get_research_settings(db).auto_resolve_evidence_exceptions:
                db.add(
                    ReviewItem(
                        project_id=project.id,
                        run_id=run.id,
                        submission_id=submission.id,
                        category="unknown_relationship",
                        message=f"Proposed relationship type '{proposal.relation_type}' requires review.",
                    )
                )
            continue
        if not proposal.claim_indexes or not supported_indexes.intersection(proposal.claim_indexes):
            continue
        supporting_index = min(supported_indexes.intersection(proposal.claim_indexes))
        left = concept_map.get(normalize_name(proposal.source)) or db.scalar(
            select(Concept).where(Concept.project_id == project.id, Concept.normalized_name == normalize_name(proposal.source))
        )
        right = concept_map.get(normalize_name(proposal.target)) or db.scalar(
            select(Concept).where(Concept.project_id == project.id, Concept.normalized_name == normalize_name(proposal.target))
        )
        if not left or not right:
            continue
        existing = db.scalar(
            select(Relationship).where(
                Relationship.project_id == project.id,
                Relationship.source_concept_id == left.id,
                Relationship.target_concept_id == right.id,
                Relationship.relation_type == proposal.relation_type,
            )
        )
        if not existing:
            db.add(
                Relationship(
                    project_id=project.id,
                    source_concept_id=left.id,
                    target_concept_id=right.id,
                    relation_type=proposal.relation_type,
                    provenance="source_supported",
                    status="supported",
                    supporting_claim_id=claims[supporting_index].id,
                )
            )
        elif existing.status != "supported":
            existing.status = "supported"
            existing.supporting_claim_id = claims[supporting_index].id


def _create_research_tasks(db: Session, run: ResearchRun, project: Project, result: AgentTaskResult) -> None:
    expansion = db.scalar(select(CourseExpansionRequest).where(CourseExpansionRequest.run_id == run.id))
    if expansion:
        from .course_expansion import filter_missing_topics

        planning_task = db.scalar(
            select(ResearchTask).where(ResearchTask.run_id == run.id, ResearchTask.role == "planning")
        )
        planning_context = json.loads(planning_task.context_json or "{}") if planning_task else {}
        topics = filter_missing_topics(
            db,
            project.id,
            result.subtopics,
            int(planning_context.get("max_missing_topics", 5)),
        )
        expansion.discovered_topics_json = json.dumps(topics, ensure_ascii=False)
        expansion.updated_at = utcnow()
        if not topics:
            expansion.status = "no_missing_topics"
            expansion.result_summary = "The requested material is already substantially covered by the current course and knowledge graph."
            expansion.completed_at = utcnow()
            run.status = "completed"
            run.completed_at = utcnow()
            crawl_job = db.scalar(select(CrawlJob).where(CrawlJob.run_id == run.id))
            if crawl_job and crawl_job.status in {"queued", "running", "paused"}:
                crawl_job.status = "cancelled"
                crawl_job.completed_at = utcnow()
                crawl_job.error = "No missing topics were found, so discovery was not needed."
            add_event(db, run.id, "course_gap_already_covered", expansion.result_summary)
            return
    else:
        topics = result.subtopics[:5] or [
            f"Foundations and prerequisite concepts for {project.topic}",
            f"Core methods and terminology in {project.topic}",
            f"Practical implementation of {project.topic}",
            f"Evaluation, limitations, and common failure modes for {project.topic}",
        ]
    task_ids: list[str] = []
    for topic in topics:
        if run.tasks_created >= max(2, run.task_budget - 2):
            break
        context = {"subtopic": topic}
        if expansion:
            context.update({"expansion_request_id": expansion.id, "gap_query": expansion.query})
        task = create_task(db, run, "research", topic, context=context)
        if task:
            # UUID defaults are assigned during INSERT. Flush before storing
            # these durable references on the expansion request.
            db.flush()
            task_ids.append(task.id)
    if expansion:
        expansion.task_ids_json = json.dumps(task_ids)
        expansion.status = "researching"
        expansion.result_summary = f"Found {len(task_ids)} missing topic{'s' if len(task_ids) != 1 else ''}; research agents are working on them."


def _maybe_create_review(db: Session, run: ResearchRun) -> bool:
    research_tasks = db.scalars(
        select(ResearchTask).where(ResearchTask.run_id == run.id, ResearchTask.role.in_(["research", "followup"]))
    ).all()
    if not research_tasks or any(task.status not in TERMINAL_TASK_STATES for task in research_tasks):
        return False
    review_exists = db.scalar(select(ResearchTask).where(ResearchTask.run_id == run.id, ResearchTask.role == "review"))
    if review_exists or run.tasks_created >= run.task_budget:
        return bool(review_exists)
    providers = [
        item[0]
        for item in db.execute(
            select(Submission.provider)
            .join(ResearchTask, ResearchTask.id == Submission.task_id)
            .where(ResearchTask.run_id == run.id, Submission.kind == "result")
        ).all()
    ]
    excluded = max(set(providers), key=providers.count) if providers else None
    task = create_task(
        db,
        run,
        "review",
        "Audit all submitted claims, citations, coverage, contradictions, and knowledge-graph relationships.",
        excluded_provider=excluded,
    )
    if task:
        run.status = "reviewing"
        return True
    return False


def _assemble_course(db: Session, run: ResearchRun) -> CourseVersion:
    project = db.get(Project, run.project_id)
    submissions = db.scalars(
        select(Submission)
        .join(ResearchTask, ResearchTask.id == Submission.task_id)
        .join(ResearchRun, ResearchRun.id == ResearchTask.run_id)
        .where(
            ResearchRun.project_id == project.id,
            ResearchTask.role.in_(["research", "followup"]),
            Submission.kind == "result",
        )
        .order_by(Submission.created_at)
    ).all()
    # Rebuild graph proposals from persisted, verified claims. This captures
    # claims promoted by a later review as well as source-supported follow-ups.
    for submission in submissions:
        result = AgentTaskResult.model_validate(json.loads(submission.payload_json))
        submission_task = db.get(ResearchTask, submission.task_id)
        submission_run = db.get(ResearchRun, submission_task.run_id) if submission_task else run
        claims = db.scalars(
            select(Claim).where(Claim.submission_id == submission.id).order_by(Claim.created_at)
        ).all()
        _upsert_graph(db, project, submission_run or run, submission, result, claims)
    lines = [f"# {project.title}", "", f"> Topic: {project.topic}", "", "## Learning path", ""]
    footnotes: list[str] = []
    unresolved_lines: list[str] = []
    footnote_index = 1
    seen_supported_claims: set[str] = set()
    seen_unresolved_claims: set[str] = set()
    section_index = 0
    for index, submission in enumerate(submissions, start=1):
        payload = json.loads(submission.payload_json)
        claims = db.scalars(select(Claim).where(Claim.submission_id == submission.id)).all()
        supported = []
        for claim in claims:
            key = normalize_name(claim.text)
            if claim.status in {"supported", "single_source", "reviewed_supported"} and key not in seen_supported_claims:
                supported.append(claim)
                seen_supported_claims.add(key)
        user_claims = [claim for claim in claims if claim.provenance == "user_authored"]
        if submission.validation_status == "supported" or supported or user_claims:
            section_index += 1
            lines.extend([f"### {section_index}. {payload.get('summary', 'Research section')}", ""])
        if submission.validation_status == "supported" and payload.get("note_section_markdown"):
            lines.extend([payload["note_section_markdown"].strip(), ""])
        if supported:
            lines.extend(["#### Evidence-backed claims", ""])
            for claim in supported:
                evidence = db.scalars(select(Evidence).where(Evidence.claim_id == claim.id, Evidence.verified.is_(True))).all()
                refs = []
                for item in evidence:
                    source = db.get(Source, item.source_id) if item.source_id else None
                    if source:
                        refs.append(f"[^{footnote_index}]")
                        locator = f", {item.locator}" if item.locator else ""
                        footnotes.append(f"[^{footnote_index}]: [{source.title or source.url}]({source.url}){locator}")
                        footnote_index += 1
                lines.append(f"- {claim.text} {' '.join(refs)}")
            lines.append("")
        if user_claims:
            lines.extend(["#### Your notes", ""])
            lines.extend([f"- {claim.text}" for claim in user_claims])
            lines.append("")
        unsupported = [
            claim
            for claim in claims
            if claim.status not in {"supported", "single_source", "reviewed_supported", "user_accepted"}
        ]
        for claim in unsupported:
            key = normalize_name(claim.text)
            if key and key not in seen_unresolved_claims and key not in seen_supported_claims:
                unresolved_lines.append(f"- {claim.text}")
                seen_unresolved_claims.add(key)
    if not submissions:
        lines.extend(["Research has not produced any accepted sections yet.", ""])
    if unresolved_lines:
        lines.extend(["## Unresolved claims", "", "These items remain provisional and are excluded from the evidence-backed notes.", ""])
        lines.extend(unresolved_lines + [""])
    conflict_messages = db.scalars(
        select(ReviewItem.message).where(
            ReviewItem.project_id == project.id,
            ReviewItem.category == "conflict",
        )
    ).all()
    if conflict_messages:
        lines.extend(["## Internally resolved evidence conflicts", "", "Atlas excluded these disputed interpretations from factual pages. No manual decision is required.", ""])
        lines.extend([f"- {message}" for message in dict.fromkeys(conflict_messages)] + [""])
    lines.extend(["## Sources", ""] + footnotes)
    lines.extend(["", "---", "Generated by Atlas Research. Agent output is synthesis; cited source passages are the evidence."])
    version = (db.scalar(select(func.max(CourseVersion.version)).where(CourseVersion.project_id == project.id)) or 0) + 1
    course = CourseVersion(project_id=project.id, run_id=run.id, version=version, markdown="\n".join(lines))
    db.add(course)
    db.flush()
    from .documentation import create_documentation_run, materialize_course_release

    release = materialize_course_release(db, course, status="draft")
    create_documentation_run(
        db,
        project.id,
        release.id,
        get_research_settings(db).documentation_experiment_budget,
        commit=False,
    )
    from .course_expansion import mark_expansion_complete

    mark_expansion_complete(
        db,
        run,
        f"Added {len(submissions)} accumulated evidence-backed research sections to course version {version}.",
    )
    return course


def build_draft(db: Session, project_id: str) -> dict:
    run = db.scalar(select(ResearchRun).where(ResearchRun.project_id == project_id).order_by(ResearchRun.created_at.desc()))
    if not run:
        return {"run_id": None, "markdown": "", "sections": []}
    submissions = db.scalars(
        select(Submission)
        .join(ResearchTask, ResearchTask.id == Submission.task_id)
        .where(
            ResearchTask.run_id == run.id,
            ResearchTask.role.in_(["research", "followup"]),
            Submission.kind == "result",
        )
        .order_by(Submission.created_at)
    ).all()
    sections = []
    markdown = ["# Provisional research draft", "", "> Content below is visible immediately but remains subject to citation validation and review.", ""]
    for index, submission in enumerate(submissions, start=1):
        payload = json.loads(submission.payload_json)
        task = db.get(ResearchTask, submission.task_id)
        section = {
            "submission_id": submission.id,
            "task_id": submission.task_id,
            "objective": task.objective if task else "Research section",
            "provider": submission.provider,
            "validation_status": submission.validation_status,
            "summary": payload.get("summary", ""),
            "note_section_markdown": payload.get("note_section_markdown", ""),
        }
        sections.append(section)
        markdown.extend(
            [
                f"## {index}. {section['summary'] or section['objective']}",
                "",
                f"> Validation: **{submission.validation_status}** · Author: **{submission.provider}**",
                "",
                section["note_section_markdown"] or "_No draft prose was submitted._",
                "",
            ]
        )
    return {"run_id": run.id, "markdown": "\n".join(markdown), "sections": sections}


def advance_run(db: Session, run: ResearchRun, completed_task: ResearchTask, result) -> None:
    project = db.get(Project, run.project_id)
    if completed_task.role == "planning" and isinstance(result, AgentTaskResult):
        _create_research_tasks(db, run, project, result)
    elif completed_task.role == "research":
        review_created = _maybe_create_review(db, run)
        remaining = db.scalar(
            select(func.count()).select_from(ResearchTask).where(
                ResearchTask.run_id == run.id,
                ResearchTask.role == "research",
                ResearchTask.status.in_(list(ACTIVE_TASK_STATES)),
            )
        )
        if not remaining and not review_created and run.tasks_created >= run.task_budget:
            _assemble_course(db, run)
            run.status = "completed"
            run.completed_at = utcnow()
            add_event(db, run.id, "run_completed", "Research budget exhausted; notes assembled without a review task.")
    elif completed_task.role == "followup":
        _assemble_course(db, run)
        run.status = "completed"
        run.completed_at = utcnow()
        add_event(db, run.id, "run_completed", "Follow-up completed; notes and graph were updated.")
    elif completed_task.role == "review" and isinstance(result, AgentReviewResult):
        if result.proposed_followups and completed_task.depth < run.followup_depth_limit and run.tasks_created < run.task_budget:
            create_task(
                db,
                run,
                "followup",
                result.proposed_followups[0],
                depth=completed_task.depth + 1,
                parent_task_id=completed_task.id,
                excluded_provider=completed_task.assigned_provider,
            )
            run.status = "running"
        else:
            _assemble_course(db, run)
            run.status = "completed"
            run.completed_at = utcnow()
            add_event(db, run.id, "run_completed", "Research, review, graph, and notes are complete.")
    db.commit()


async def accept_submission(
    db: Session,
    task: ResearchTask,
    provider: str,
    cli_version: str | None,
    prompt_version: str,
    payload: dict,
) -> Submission:
    if task.status not in {"leased", "running"}:
        raise ValueError("task is not active")
    if task.assigned_provider and task.assigned_provider != provider:
        raise ValueError("task is assigned to another provider")
    run = db.get(ResearchRun, task.run_id)
    if task.role == "review":
        parsed = AgentReviewResult.model_validate(payload)
        kind = "review"
    else:
        parsed = AgentTaskResult.model_validate(payload)
        kind = "result"
    submission = Submission(
        task_id=task.id,
        provider=provider,
        kind=kind,
        payload_json=parsed.model_dump_json(),
        prompt_version=prompt_version,
        cli_version=cli_version,
        same_provider_review=task.role == "review" and provider == task.excluded_provider,
    )
    db.add(submission)
    db.flush()
    if isinstance(parsed, AgentTaskResult):
        await persist_research_result(db, task, submission, parsed)
    else:
        submission.validation_status = "reviewed"
        for claim_id in parsed.accepted_claim_ids:
            claim = db.get(Claim, claim_id)
            if claim and claim.status in {"supported", "single_source"}:
                claim.status = "reviewed_supported"
        for claim_id in parsed.rejected_claim_ids:
            claim = db.get(Claim, claim_id)
            if claim:
                claim.status = "rejected"
                if claim.submission_id:
                    author_submission = db.get(Submission, claim.submission_id)
                    if author_submission:
                        author_submission.validation_status = "mixed"
                for concept in db.scalars(select(Concept).where(Concept.supporting_claim_id == claim.id)).all():
                    concept.status = "rejected"
                for relationship in db.scalars(select(Relationship).where(Relationship.supporting_claim_id == claim.id)).all():
                    relationship.status = "rejected"
                if not get_research_settings(db).auto_resolve_evidence_exceptions:
                    db.add(
                        ReviewItem(
                            project_id=run.project_id,
                            run_id=run.id,
                            submission_id=submission.id,
                            claim_id=claim.id,
                            category="review_rejection",
                            message=f"Reviewer rejected claim: {claim.text}",
                        )
                    )
        for message in parsed.citation_problems:
            if not get_research_settings(db).auto_resolve_evidence_exceptions:
                db.add(
                    ReviewItem(
                        project_id=run.project_id,
                        run_id=run.id,
                        submission_id=submission.id,
                        category="review_citation_problem",
                        message=message,
                    )
                )
        for message in parsed.conflicts:
            db.add(
                ReviewItem(
                    project_id=run.project_id,
                    run_id=run.id,
                    submission_id=submission.id,
                    category="conflict",
                    message=message,
                    status=("auto_resolved" if get_research_settings(db).auto_resolve_evidence_exceptions else "open"),
                    decision=("retained_as_unresolved" if get_research_settings(db).auto_resolve_evidence_exceptions else None),
                )
            )
        for message in parsed.corrections:
            if not get_research_settings(db).auto_resolve_evidence_exceptions:
                db.add(
                    ReviewItem(
                        project_id=run.project_id,
                        run_id=run.id,
                        submission_id=submission.id,
                        category="review_correction",
                        message=message,
                    )
                )
    task.status = "completed"
    task.completed_at = utcnow()
    task.lease_expires_at = None
    add_event(db, task.run_id, "task_completed", f"{provider} completed {task.role} task", {"task_id": task.id})
    execution = db.scalar(
        select(AgentExecution)
        .where(AgentExecution.task_id == task.id, AgentExecution.status.in_(["running", "cancel_requested"]))
        .order_by(AgentExecution.started_at.desc())
    )
    if execution:
        execution.status = "completed"
        execution.completed_at = utcnow()
        execution.exit_code = 0
    db.commit()
    db.refresh(submission)
    advance_run(db, run, task, parsed)
    return submission


def fail_task(db: Session, task: ResearchTask, runner_id: str, diagnostic: str, exit_code: int | None) -> None:
    if task.leased_by != runner_id:
        raise ValueError("task is leased by another runner")
    execution = db.scalar(
        select(AgentExecution)
        .where(AgentExecution.task_id == task.id, AgentExecution.status.in_(["running", "cancel_requested"]))
        .order_by(AgentExecution.started_at.desc())
    )
    cancel_requested = bool(
        execution
        and (execution.cancel_requested_at or execution.status == "cancel_requested")
    ) or "execution cancelled" in diagnostic.casefold()
    rate_limited = bool(re.search(r"rate.?limit|\b429\b|quota|usage limit", diagnostic, re.IGNORECASE))
    if rate_limited:
        # A transient Azure deployment throttle must not force a model
        # fallback. Keep retries bounded, but give the single allowed model
        # enough time to recover.
        task.max_attempts = max(task.max_attempts, 5)
    if execution:
        execution.status = "cancelled" if cancel_requested else "failed"
        execution.completed_at = utcnow()
        execution.exit_code = exit_code
        execution.diagnostic = diagnostic[:4000]
    if cancel_requested:
        task.status = "cancelled"
        task.completed_at = utcnow()
        task.lease_expires_at = None
        add_event(db, task.run_id, "task_cancelled", f"Cancelled {task.role} execution.", {"task_id": task.id})
    elif task.attempts < task.max_attempts:
        task.status = "queued"
        run = db.get(ResearchRun, task.run_id)
        task.assigned_provider = run.provider_mode if run else None
        task.leased_by = None
        task.lease_expires_at = None
        task.available_after = (
            utcnow() + timedelta(seconds=min(300, 30 * (2 ** max(0, task.attempts - 1))))
            if rate_limited
            else None
        )
        add_event(db, task.run_id, "task_requeued", f"Task failed and will be retried: {diagnostic[:200]}")
    else:
        task.status = "failed"
        task.completed_at = utcnow()
        add_event(db, task.run_id, "task_failed", f"Task failed permanently: {diagnostic[:200]}")
        run = db.get(ResearchRun, task.run_id)
        if task.role == "planning":
            run.status = "failed"
            run.completed_at = utcnow()
            run.stop_reason = diagnostic[:1000]
        else:
            _maybe_create_review(db, run)
    db.commit()
