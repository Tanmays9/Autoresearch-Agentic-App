from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class ProjectCreate(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    topic: str = Field(min_length=2, max_length=4000)
    goal: str = Field(default="Create a cited, prerequisite-ordered course", max_length=4000)
    learner_level: str = Field(default="Python and basic ML", max_length=160)
    start_immediately: bool = False


class ResearchStart(BaseModel):
    task_budget: int = Field(default=20, ge=3, le=24)
    source_budget: int = Field(default=200, ge=1, le=200)
    followup_depth_limit: int = Field(default=2, ge=0, le=4)
    provider_mode: Literal["inhouse_azure", "codex"] | None = None


class ResearchSettingsPatch(BaseModel):
    codex_concurrency: int | None = Field(default=None, ge=1, le=5)
    codex_web_research: bool | None = None
    default_source_budget: int | None = Field(default=None, ge=1, le=200)
    crawler_concurrency: int | None = Field(default=None, ge=1, le=16)
    crawler_per_domain: int | None = Field(default=None, ge=1, le=4)


class AgentSettingsPatch(BaseModel):
    provider_mode: Literal["inhouse_azure", "codex"] | None = None
    inhouse_agent_concurrency: int | None = Field(default=None, ge=1, le=5)
    inhouse_tool_rounds: int | None = Field(default=None, ge=1, le=12)
    default_task_budget: int | None = Field(default=None, ge=3, le=24)
    documentation_experiment_budget: int | None = Field(default=None, ge=1, le=12)
    azure_token_budget: int | None = Field(default=None, ge=1000, le=50_000_000)
    azure_cost_budget_usd: float | None = Field(default=None, ge=0.1, le=10_000)
    codex_fallback: bool | None = None
    auto_accept_verified_single_source: bool | None = None
    auto_resolve_evidence_exceptions: bool | None = None


class CourseExpansionCreate(BaseModel):
    query: str = Field(min_length=3, max_length=4000)
    max_topics: int = Field(default=5, ge=1, le=5)
    source_budget: int = Field(default=100, ge=1, le=200)


class DocumentationRunCreate(BaseModel):
    base_release_id: str | None = None
    experiment_budget: int = Field(default=12, ge=1, le=12)


class DocumentationDecisionInput(BaseModel):
    note: str | None = Field(default=None, max_length=4000)
    actor: str = Field(default="local_user", max_length=160)


class RunnerRegister(BaseModel):
    runner_id: str = Field(min_length=3, max_length=80)
    hostname: str = Field(min_length=1, max_length=160)
    providers: list[dict[str, Any]]


class RunnerHeartbeat(BaseModel):
    runner_id: str
    busy_providers: list[str] = []


class KiroLaunchClaim(BaseModel):
    runner_id: str


class TaskClaimRequest(BaseModel):
    runner_id: str
    provider: Literal["codex", "claude", "gemini", "kiro"]
    cli_version: str | None = None


class TaskHeartbeat(BaseModel):
    runner_id: str
    provider: str


class ExecutionEventInput(BaseModel):
    stream: Literal["stdout", "stderr", "system"] = "stdout"
    event_type: str = Field(default="raw", min_length=1, max_length=80)
    content: str = Field(max_length=65536)


class ExecutionEventBatch(BaseModel):
    runner_id: str
    events: list[ExecutionEventInput] = Field(min_length=1, max_length=100)


class TaskRelease(BaseModel):
    runner_id: str
    provider: str
    reason: str = Field(default="released by runner", max_length=1000)


class EvidenceInput(BaseModel):
    # Azure strict structured output does not accept JSON Schema's `uri`
    # format. Atlas independently performs public-address/SSRF validation
    # before fetching, so the agent contract uses a strict web-URL pattern.
    url: str = Field(min_length=8, max_length=4000, pattern=r"^https?://")
    quote: str = Field(min_length=8, max_length=16000)
    locator: str | None = Field(default=None, max_length=1000)
    title: str | None = Field(default=None, max_length=1000)


class ClaimInput(BaseModel):
    text: str = Field(min_length=3, max_length=12000)
    provenance: Literal["source_supported", "llm_synthesis", "llm_hypothesis", "user_authored"] = "llm_synthesis"
    evidence: list[EvidenceInput] = []


class ConceptInput(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    concept_type: str = Field(default="concept", max_length=60)
    summary: str = Field(default="", max_length=8000)
    claim_indexes: list[int] = []


RELATION_TYPES = {
    "prerequisite_of",
    "part_of",
    "type_of",
    "uses",
    "produces",
    "improves",
    "limits",
    "causes",
    "mitigates",
    "evaluated_by",
    "contrasts_with",
    "related_to",
}


class RelationshipInput(BaseModel):
    source: str = Field(min_length=1, max_length=240)
    target: str = Field(min_length=1, max_length=240)
    relation_type: str = Field(max_length=60)
    claim_indexes: list[int] = []


class SourceCandidateInput(BaseModel):
    url: str = Field(min_length=8, max_length=4000, pattern=r"^https?://")
    title: str | None = Field(default=None, max_length=1000)
    query: str | None = Field(default=None, max_length=1000)
    relevance_reason: str | None = Field(default=None, max_length=4000)


class AgentTaskResult(BaseModel):
    summary: str = Field(min_length=1, max_length=30000)
    subtopics: list[str] = Field(default=[], max_length=12)
    claims: list[ClaimInput] = Field(default=[], max_length=80)
    concepts: list[ConceptInput] = Field(default=[], max_length=80)
    relationships: list[RelationshipInput] = Field(default=[], max_length=120)
    source_candidates: list[SourceCandidateInput] = Field(default=[], max_length=80)
    note_section_markdown: str = Field(default="", max_length=120000)
    gaps: list[str] = Field(default=[], max_length=30)
    proposed_followups: list[str] = Field(default=[], max_length=12)


class AgentReviewResult(BaseModel):
    summary: str = Field(min_length=1, max_length=30000)
    accepted_claim_ids: list[str] = []
    rejected_claim_ids: list[str] = []
    citation_problems: list[str] = []
    conflicts: list[str] = []
    corrections: list[str] = []
    proposed_followups: list[str] = Field(default=[], max_length=8)


class TaskSubmitRequest(BaseModel):
    runner_id: str
    provider: str
    cli_version: str | None = None
    prompt_version: str = "research-v1"
    result: dict[str, Any]


class TaskFailRequest(BaseModel):
    runner_id: str
    provider: str
    exit_code: int | None = None
    diagnostic: str = Field(max_length=4000)


class McpClaimRequest(BaseModel):
    task_id: str | None = None
    agent_name: str
    provider: Literal["codex", "claude", "gemini", "kiro"] = "kiro"
    lease_seconds: int = Field(default=900, ge=60, le=3600)


class ReviewDecision(BaseModel):
    decision: Literal["reject", "accept_as_user_authored", "research_further", "resolved"]
    note: str | None = Field(default=None, max_length=4000)


class FollowupCreate(BaseModel):
    parent_task_id: str
    objective: str = Field(min_length=4, max_length=4000)


class ManualSourceCreate(BaseModel):
    url: HttpUrl
    title: str | None = None
    trusted: bool = False


class PublicProject(BaseModel):
    id: str
    title: str
    topic: str
    goal: str
    learner_level: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
