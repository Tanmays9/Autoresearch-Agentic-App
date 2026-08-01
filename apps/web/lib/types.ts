export type Project = {
  id: string;
  title: string;
  topic: string;
  goal: string;
  learner_level: string;
  created_at: string;
  updated_at: string;
};

export type Run = {
  id: string;
  status: string;
  task_budget: number;
  source_budget: number;
  tasks_created: number;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  provider_mode?: string;
  langgraph_thread_id?: string;
  token_budget?: number;
  tokens_used?: number;
  cost_budget_usd?: number;
  cost_used_usd?: number;
};

export type Task = {
  id: string;
  run_id: string;
  role: string;
  objective: string;
  status: string;
  depth: number;
  provider?: string;
  lease_expires_at?: string;
  attempts?: number;
  max_attempts?: number;
  available_after?: string;
};

export type ExecutionEvent = {
  id: number;
  sequence: number;
  stream: "stdout" | "stderr" | "system";
  event_type: string;
  content: string;
  created_at: string;
};

export type AgentExecution = {
  id: string;
  task_id: string;
  runner_id: string;
  provider: string;
  status: string;
  started_at: string;
  completed_at?: string;
  last_heartbeat_at?: string;
  cancel_requested_at?: string;
  exit_code?: number;
  diagnostic?: string;
  output_bytes: number;
  model?: string;
  langgraph_thread_id?: string;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  tool_calls?: string[];
  task?: Task;
  result?: Record<string, unknown>;
  events?: ExecutionEvent[];
};

export type CourseRelease = {
  id: string;
  project_id: string;
  version: number;
  title: string;
  summary: string;
  status: string;
  created_at: string;
  published_at?: string;
  page_count: number;
};

export type CoursePageSummary = {
  id: string;
  page_id: string;
  parent_page_id?: string;
  release_id: string;
  release_version: number;
  release_status: string;
  slug: string;
  title: string;
  page_type: string;
  position: number;
  summary: string;
  status: string;
  quality_score: number;
  content_provenance: string;
  headings: Array<{ level: number; title: string; anchor: string }>;
};

export type CoursePage = CoursePageSummary & {
  markdown: string;
  claims: Array<{ id: string; text: string; provenance: string; status: string }>;
  sources: Array<{ id: string; title?: string; url: string }>;
  previous?: CoursePageSummary;
  next?: CoursePageSummary;
  release: CourseRelease;
};

export type DocumentationExperiment = {
  id: string;
  page_id: string;
  strategy: string;
  hypothesis: string;
  baseline_score: number;
  candidate_score: number;
  status: string;
  outcome?: string;
  model: string;
};

export type DocumentationRun = {
  id: string;
  project_id: string;
  base_release_id: string;
  candidate_release_id?: string;
  status: string;
  experiment_budget: number;
  langgraph_thread_id: string;
  run_type?: string;
  instructions?: string;
  allow_llm_synthesis?: boolean;
  feedback_id?: string;
  error?: string;
  created_at: string;
  completed_at?: string;
  experiments: DocumentationExperiment[];
};

export type CourseExpansionRequest = {
  id: string;
  project_id: string;
  run_id?: string;
  query: string;
  status: string;
  discovered_topics: string[];
  task_ids: string[];
  result_summary?: string;
  error?: string;
  created_at: string;
  updated_at: string;
  completed_at?: string;
  tasks: Array<{ id: string; role: string; objective: string; status: string; assigned_provider?: string }>;
};

export type CourseFeedback = {
  id: string;
  project_id: string;
  release_id?: string;
  page_id?: string;
  page_title?: string;
  kind: "add" | "remove" | "improve" | "restructure";
  message: string;
  status: string;
  result_summary?: string;
  documentation_run_id?: string;
  error?: string;
  created_at: string;
  updated_at: string;
  completed_at?: string;
};

export type ProjectObjective = {
  project_id: string;
  objective: string;
  audience: string;
  success_criteria: string[];
  required_topics: Array<{
    title: string;
    description?: string;
    recommended_slug?: string;
    page_type?: string;
    status: string;
    reason?: string;
  }>;
  coverage: Array<{ title: string; slug?: string; status: string; reason?: string }>;
  status: string;
  iteration: number;
  completion_score: number;
  allow_llm_synthesis: boolean;
  last_reviewed_at?: string;
  created_at: string;
  updated_at: string;
};

export type ProjectRunHistory = Run & {
  sequence: number;
  kind: "research" | "course_gap";
  label: string;
  task_count: number;
  completed_task_count: number;
  tasks: Task[];
};

export type ReviewDetail = {
  id: string;
  category: string;
  status: string;
  message: string;
  decision?: string;
  created_at: string;
  claim?: { id: string; text: string; provenance: string; status: string };
  evidence: Array<{
    id: string;
    quote: string;
    locator?: string;
    verified: boolean;
    error?: string;
    source?: { id: string; url: string; title?: string; status: string };
  }>;
  submission?: { id: string; provider: string; validation_status: string; summary: string; note_section_markdown: string };
  task?: Task;
  execution?: AgentExecution;
  concepts: Array<{ id: string; name: string; type: string }>;
  relationships: Array<{ id: string; type: string; source: string; target: string }>;
};

export type CrawlJob = {
  id: string;
  run_id: string;
  status: string;
  max_pages: number;
  discovered_count: number;
  processed_count: number;
  fetched_count: number;
  failed_count: number;
  skipped_count: number;
  error?: string;
  targets: Array<{
    id: string;
    source_id?: string;
    url: string;
    title?: string;
    depth: number;
    domain: string;
    status: string;
    relevance_score?: number;
    error?: string;
  }>;
};

export type Agent = {
  provider: string;
  status: string;
  version?: string;
  mode: string;
  diagnostic?: string;
};

export type ProjectDetail = {
  project: Project;
  objective: ProjectObjective;
  run: Run | null;
  run_history: ProjectRunHistory[];
  tasks: Task[];
  submissions: Array<{
    id: string;
    task_id: string;
    provider: string;
    kind: string;
    validation_status: string;
    same_provider_review: boolean;
    payload: Record<string, unknown>;
  }>;
  sources: Array<{ id: string; url: string; title?: string; status: string; trust_level: string }>;
  reviews: Array<{ id: string; category: string; status: string; message: string; decision?: string }>;
  events: Array<{ id: number; type: string; message: string; created_at: string }>;
  graph: {
    nodes: Array<{ id: string; name: string; type: string; summary: string; provenance: string }>;
    edges: Array<{ id: string; source: string; target: string; type: string; status: string }>;
  };
  course: { version: number; markdown: string; created_at: string } | null;
};
