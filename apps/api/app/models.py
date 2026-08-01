from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid4str() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    title: Mapped[str] = mapped_column(String(240))
    topic: Mapped[str] = mapped_column(Text)
    goal: Mapped[str] = mapped_column(Text, default="Create a cited, prerequisite-ordered course")
    learner_level: Mapped[str] = mapped_column(String(80), default="Python and basic ML")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    runs: Mapped[list[ResearchRun]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectObjective(Base):
    """Durable definition of what a project's course is trying to achieve.

    The coverage fields are updated by the LangGraph course-completion agent on
    every bounded iteration.  Atlas keeps this separate from an individual
    research run so follow-up work never replaces the project's intent.
    """

    __tablename__ = "project_objectives"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    objective: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(240), default="Python and basic ML")
    success_criteria_json: Mapped[str] = mapped_column(Text, default="[]")
    required_topics_json: Mapped[str] = mapped_column(Text, default="[]")
    coverage_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    completion_score: Mapped[float] = mapped_column(Float, default=0.0)
    allow_llm_synthesis: Mapped[bool] = mapped_column(Boolean, default=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    task_budget: Mapped[int] = mapped_column(Integer, default=8)
    source_budget: Mapped[int] = mapped_column(Integer, default=200)
    followup_depth_limit: Mapped[int] = mapped_column(Integer, default=2)
    provider_mode: Mapped[str] = mapped_column(String(32), default="inhouse_azure", index=True)
    langgraph_thread_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    token_budget: Mapped[int] = mapped_column(Integer, default=1_000_000)
    cost_budget_usd: Mapped[float] = mapped_column(Float, default=50.0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    cost_used_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tasks_created: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="runs")
    tasks: Mapped[list[ResearchTask]] = relationship(back_populates="run", cascade="all, delete-orphan")


class ResearchTask(Base):
    __tablename__ = "research_tasks"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_task_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"), index=True)
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("research_tasks.id"), nullable=True)
    review_of_task_id: Mapped[str | None] = mapped_column(ForeignKey("research_tasks.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    assigned_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    excluded_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    idempotency_key: Mapped[str] = mapped_column(String(180))
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[ResearchRun] = relationship(back_populates="tasks", foreign_keys=[run_id])
    submissions: Mapped[list[Submission]] = relationship(back_populates="task", cascade="all, delete-orphan")


class AgentInstallation(Base):
    __tablename__ = "agent_installations"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(40), default="not_installed")
    version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    mode: Mapped[str] = mapped_column(String(40), default="headless")
    capabilities_json: Mapped[str] = mapped_column(Text, default="{}")
    diagnostic: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RunnerRegistration(Base):
    __tablename__ = "runner_registrations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="available")
    capabilities_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentExecution(Base):
    __tablename__ = "agent_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    task_id: Mapped[str] = mapped_column(ForeignKey("research_tasks.id", ondelete="CASCADE"), index=True)
    runner_id: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="running")
    command_json: Mapped[str] = mapped_column(Text, default="[]")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diagnostic: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    output_bytes: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    langgraph_thread_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tool_calls_json: Mapped[str] = mapped_column(Text, default="[]")


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    __table_args__ = (UniqueConstraint("execution_id", "sequence", name="uq_execution_event_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("agent_executions.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    stream: Mapped[str] = mapped_column(String(16), default="stdout")
    event_type: Mapped[str] = mapped_column(String(80), default="raw")
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchSettings(Base):
    __tablename__ = "research_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    codex_concurrency: Mapped[int] = mapped_column(Integer, default=3)
    codex_web_research: Mapped[bool] = mapped_column(Boolean, default=True)
    default_source_budget: Mapped[int] = mapped_column(Integer, default=200)
    crawler_concurrency: Mapped[int] = mapped_column(Integer, default=8)
    crawler_per_domain: Mapped[int] = mapped_column(Integer, default=2)
    provider_mode: Mapped[str] = mapped_column(String(32), default="inhouse_azure")
    inhouse_agent_concurrency: Mapped[int] = mapped_column(Integer, default=5)
    inhouse_tool_rounds: Mapped[int] = mapped_column(Integer, default=6)
    default_task_budget: Mapped[int] = mapped_column(Integer, default=20)
    documentation_experiment_budget: Mapped[int] = mapped_column(Integer, default=12)
    azure_token_budget: Mapped[int] = mapped_column(Integer, default=1_000_000)
    azure_cost_budget_usd: Mapped[float] = mapped_column(Float, default=50.0)
    codex_fallback: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_accept_verified_single_source: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_resolve_evidence_exceptions: Mapped[bool] = mapped_column(Boolean, default=True)
    # Retained for backwards-compatible persistence only. Atlas never honors
    # this as an authorization to publish; every candidate requires a human
    # approval decision through the LangGraph interrupt.
    auto_publish_documentation: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    task_id: Mapped[str] = mapped_column(ForeignKey("research_tasks.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32), default="result")
    payload_json: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(String(40), default="research-v1")
    cli_version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    validation_status: Mapped[str] = mapped_column(String(32), default="pending")
    same_provider_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[ResearchTask] = relationship(back_populates="submissions")


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("project_id", "url", name="uq_project_source_url"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="web")
    status: Mapped[str] = mapped_column(String(32), default="discovered")
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trust_level: Mapped[str] = mapped_column(String(32), default="unreviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceChunk(Base):
    __tablename__ = "source_chunks"
    __table_args__ = (UniqueConstraint("snapshot_id", "chunk_index", name="uq_source_chunk_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    max_pages: Mapped[int] = mapped_column(Integer, default=200)
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CrawlTarget(Base):
    __tablename__ = "crawl_targets"
    __table_args__ = (UniqueConstraint("job_id", "url", name="uq_crawl_target_url"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    job_id: Mapped[str] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    parent_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("research_tasks.id", ondelete="SET NULL"), nullable=True)
    submission_id: Mapped[str | None] = mapped_column(ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String(32), default="llm_hypothesis")
    status: Mapped[str] = mapped_column(String(32), default="unsupported")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    quote: Mapped[str] = mapped_column(Text)
    locator: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Concept(Base):
    __tablename__ = "concepts"
    __table_args__ = (UniqueConstraint("project_id", "normalized_name", name="uq_project_concept_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(240))
    normalized_name: Mapped[str] = mapped_column(String(240))
    concept_type: Mapped[str] = mapped_column(String(60), default="concept")
    summary: Mapped[str] = mapped_column(Text, default="")
    provenance: Mapped[str] = mapped_column(String(32), default="llm_synthesis")
    status: Mapped[str] = mapped_column(String(32), default="supported")
    supporting_claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id", ondelete="SET NULL"), nullable=True)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("project_id", "source_concept_id", "target_concept_id", "relation_type", name="uq_relationship"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_concept_id: Mapped[str] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"))
    target_concept_id: Mapped[str] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"))
    relation_type: Mapped[str] = mapped_column(String(60))
    provenance: Mapped[str] = mapped_column(String(32), default="llm_synthesis")
    status: Mapped[str] = mapped_column(String(32), default="supported")
    supporting_claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id", ondelete="SET NULL"), nullable=True)


class CourseVersion(Base):
    __tablename__ = "course_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_course_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CourseExpansionRequest(Base):
    __tablename__ = "course_expansion_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    query: Mapped[str] = mapped_column(Text)
    normalized_query: Mapped[str] = mapped_column(Text, index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    discovered_topics_json: Mapped[str] = mapped_column(Text, default="[]")
    task_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CourseFeedback(Base):
    __tablename__ = "course_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    release_id: Mapped[str | None] = mapped_column(ForeignKey("course_releases.id", ondelete="SET NULL"), nullable=True)
    page_id: Mapped[str | None] = mapped_column(ForeignKey("course_pages.id", ondelete="SET NULL"), nullable=True)
    documentation_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("documentation_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str] = mapped_column(Text)
    allow_llm_synthesis: Mapped[bool] = mapped_column(Boolean, default=True)
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentProfile(Base):
    __tablename__ = "agent_profiles"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    role: Mapped[str] = mapped_column(String(60), index=True)
    model: Mapped[str] = mapped_column(String(160))
    tools_json: Mapped[str] = mapped_column(Text, default="[]")
    max_tool_rounds: Mapped[int] = mapped_column(Integer, default=6)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    prompt_version: Mapped[str] = mapped_column(String(40), default="langgraph-v1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CourseRelease(Base):
    __tablename__ = "course_releases"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_course_release_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True)
    legacy_course_version_id: Mapped[str | None] = mapped_column(ForeignKey("course_versions.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CoursePage(Base):
    __tablename__ = "course_pages"
    __table_args__ = (UniqueConstraint("project_id", "stable_key", name="uq_course_page_stable_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    stable_key: Mapped[str] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CoursePageVersion(Base):
    __tablename__ = "course_page_versions"
    __table_args__ = (
        UniqueConstraint("release_id", "slug", name="uq_course_page_version_slug"),
        UniqueConstraint("release_id", "page_id", name="uq_course_page_release_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    page_id: Mapped[str] = mapped_column(ForeignKey("course_pages.id", ondelete="CASCADE"), index=True)
    release_id: Mapped[str] = mapped_column(ForeignKey("course_releases.id", ondelete="CASCADE"), index=True)
    parent_page_id: Mapped[str | None] = mapped_column(ForeignKey("course_pages.id", ondelete="SET NULL"), nullable=True)
    slug: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(240))
    page_type: Mapped[str] = mapped_column(String(60), default="core_concept")
    position: Mapped[int] = mapped_column(Integer, default=0)
    markdown: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    headings_json: Mapped[str] = mapped_column(Text, default="[]")
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    content_provenance: Mapped[str] = mapped_column(String(32), default="source_supported")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CoursePageClaim(Base):
    __tablename__ = "course_page_claims"
    __table_args__ = (UniqueConstraint("page_version_id", "claim_id", name="uq_course_page_claim"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    page_version_id: Mapped[str] = mapped_column(ForeignKey("course_page_versions.id", ondelete="CASCADE"), index=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)


class CoursePageSource(Base):
    __tablename__ = "course_page_sources"
    __table_args__ = (UniqueConstraint("page_version_id", "source_id", name="uq_course_page_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    page_version_id: Mapped[str] = mapped_column(ForeignKey("course_page_versions.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)


class DocumentationRun(Base):
    __tablename__ = "documentation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    base_release_id: Mapped[str] = mapped_column(ForeignKey("course_releases.id", ondelete="CASCADE"))
    candidate_release_id: Mapped[str | None] = mapped_column(ForeignKey("course_releases.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    experiment_budget: Mapped[int] = mapped_column(Integer, default=12)
    langgraph_thread_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    requested_by: Mapped[str] = mapped_column(String(80), default="user")
    run_type: Mapped[str] = mapped_column(String(32), default="improvement", index=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    allow_llm_synthesis: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentationExperiment(Base):
    __tablename__ = "documentation_experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    documentation_run_id: Mapped[str] = mapped_column(ForeignKey("documentation_runs.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[str] = mapped_column(ForeignKey("course_pages.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(40))
    hypothesis: Mapped[str] = mapped_column(Text)
    baseline_markdown: Mapped[str] = mapped_column(Text)
    candidate_markdown: Mapped[str] = mapped_column(Text, default="")
    source_snapshot_json: Mapped[str] = mapped_column(Text, default="[]")
    baseline_metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    candidate_metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    baseline_score: Mapped[float] = mapped_column(Float, default=0.0)
    candidate_score: Mapped[float] = mapped_column(Float, default=0.0)
    model: Mapped[str] = mapped_column(String(160))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    documentation_run_id: Mapped[str] = mapped_column(ForeignKey("documentation_runs.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(160), default="local_user")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True)
    submission_id: Mapped[str | None] = mapped_column(ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id", ondelete="SET NULL"), nullable=True)
    category: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(32), default="open")
    message: Mapped[str] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text)
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
