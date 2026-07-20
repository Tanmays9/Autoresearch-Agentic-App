"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  BookOpen,
  Bot,
  BrainCircuit,
  Check,
  CircleAlert,
  Clock3,
  Download,
  FileSearch,
  GitFork,
  LoaderCircle,
  Network,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Square,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import Link from "next/link";

import { AgentRuns } from "@/components/agent-runs";
import { GraphView } from "@/components/graph-view";
import { ReviewWorkbench } from "@/components/review-workbench";
import { SourceExplorer } from "@/components/source-explorer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { API_URL, api } from "@/lib/api";
import type { Agent, Project, ProjectDetail, Task } from "@/lib/types";
import { cn } from "@/lib/utils";

type Tab = "overview" | "agents" | "graph" | "notes" | "reviews" | "sources";

export function Dashboard() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [topic, setTopic] = useState("");
  const [tab, setTab] = useState<Tab>("overview");
  const [submitting, setSubmitting] = useState(false);
  const [codexBusy, setCodexBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadProjects = useCallback(async () => {
    const values = await api<Project[]>("/api/v1/projects");
    setProjects(values);
    setSelectedId((current) => current ?? values[0]?.id ?? null);
  }, []);

  const loadAgents = useCallback(async () => {
    const values = await api<{ agents: Agent[] }>("/api/v1/agents");
    setAgents(values.agents);
  }, []);

  const loadDetail = useCallback(async (projectId: string) => {
    const value = await api<ProjectDetail>(`/api/v1/projects/${projectId}`);
    setDetail(value);
  }, []);

  useEffect(() => {
    Promise.all([loadProjects(), loadAgents()]).catch((reason) => setError(reason.message));
  }, [loadAgents, loadProjects]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    loadDetail(selectedId).catch((reason) => setError(reason.message));
    const detailTimer = setInterval(() => loadDetail(selectedId).catch(() => {}), 3500);
    const agentTimer = setInterval(() => loadAgents().catch(() => {}), 10000);
    return () => {
      clearInterval(detailTimer);
      clearInterval(agentTimer);
    };
  }, [loadAgents, loadDetail, selectedId]);

  async function createTopic(event: FormEvent) {
    event.preventDefault();
    const clean = topic.trim();
    if (!clean) return;
    setSubmitting(true);
    setError(null);
    try {
      const value = await api<{ project: Project }>("/api/v1/projects", {
        method: "POST",
        body: JSON.stringify({ title: clean.slice(0, 80), topic: clean, start_immediately: true }),
      });
      setTopic("");
      await loadProjects();
      setSelectedId(value.project.id);
      setTab("overview");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create topic");
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelRun() {
    if (!detail?.run) return;
    await api(`/api/v1/research-runs/${detail.run.id}/cancel`, { method: "POST" });
    await loadDetail(detail.project.id);
  }

  async function invokeCodex() {
    if (!detail) return;
    setCodexBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api<{ message: string }>(`/api/v1/projects/${detail.project.id}/research-with-codex`, { method: "POST" });
      setNotice(`${result.message}. The local Codex CLI worker will claim it automatically.`);
      await loadDetail(detail.project.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not queue Codex research");
    } finally {
      setCodexBusy(false);
    }
  }

  return (
    <div className="min-h-screen w-full max-w-full overflow-x-clip">
      <aside className="border-b border-border bg-[#17231f] text-white lg:fixed lg:inset-y-0 lg:left-0 lg:z-30 lg:h-dvh lg:w-[250px] lg:overflow-y-auto lg:border-b-0 lg:border-r xl:w-[260px]">
        <div className="flex items-center gap-3 border-b border-white/10 px-5 py-5">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-[#d9a74a] text-[#17231f]">
            <BrainCircuit size={22} strokeWidth={2.4} />
          </div>
          <div>
            <p className="font-serif text-lg font-semibold">Atlas Research</p>
            <p className="text-xs text-white/50">Evidence before synthesis</p>
          </div>
        </div>

        <div className="px-3 py-5">
          <p className="px-3 text-[10px] font-bold uppercase tracking-[.18em] text-white/40">Research library</p>
          <div className="mt-3 space-y-1">
            {projects.map((project) => (
              <button
                key={project.id}
                onClick={() => { setSelectedId(project.id); setTab("overview"); }}
                className={cn(
                  "w-full rounded-xl px-3 py-3 text-left transition",
                  selectedId === project.id ? "bg-white/12 text-white" : "text-white/65 hover:bg-white/5 hover:text-white",
                )}
              >
                <p className="truncate text-sm font-semibold">{project.title}</p>
                <p className="mt-1 truncate text-xs text-white/40">{project.learner_level}</p>
              </button>
            ))}
            {!projects.length && <p className="px-3 py-4 text-sm leading-6 text-white/45">Your research topics will appear here.</p>}
          </div>
        </div>

        <div className="mx-4 mt-auto rounded-2xl border border-white/10 bg-white/5 p-4 lg:absolute lg:bottom-5 lg:left-0 lg:right-0">
          <div className="flex items-center gap-2 text-xs font-semibold text-white/80">
            <ShieldCheck size={15} className="text-emerald-300" /> Local and bounded
          </div>
          <p className="mt-2 text-xs leading-5 text-white/45">Agents can submit research, but only verified source passages become evidence.</p>
        </div>
      </aside>

      <main className="w-full min-w-0 max-w-full overflow-x-hidden lg:ml-[250px] lg:w-[calc(100%_-_250px)] xl:ml-[260px] xl:w-[calc(100%_-_260px)]">
        <header className="min-w-0 border-b border-border bg-white/75 px-4 py-4 backdrop-blur-xl md:px-6 xl:px-8">
          <form onSubmit={createTopic} className="mx-auto flex w-full min-w-0 max-w-6xl flex-col gap-3 sm:flex-row">
            <div className="relative flex-1">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
              <Input
                aria-label="Research topic"
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                placeholder="Research any topic — for example, evaluating QLoRA fine-tunes"
                className="h-12 pl-11 shadow-sm"
              />
            </div>
            <Button size="lg" className="shrink-0" disabled={submitting || !topic.trim()}>
              {submitting ? <LoaderCircle className="animate-spin" size={17} /> : <Plus size={17} />}
              Research
            </Button>
          </form>
        </header>

        <div className="mx-auto w-full min-w-0 max-w-6xl px-4 py-7 sm:px-5 md:px-6 md:py-9 xl:px-8">
          {error && (
            <div className="mb-5 flex items-center justify-between rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              <span className="flex items-center gap-2"><CircleAlert size={16} /> {error}</span>
              <button onClick={() => setError(null)} aria-label="Dismiss error">×</button>
            </div>
          )}
          {notice && (
            <div className="mb-5 flex items-center justify-between rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
              <span className="flex items-center gap-2"><Check size={16} /> {notice}</span>
              <button onClick={() => setNotice(null)} aria-label="Dismiss notice">×</button>
            </div>
          )}
          {!detail ? <Welcome agents={agents} /> : (
            <>
              <ProjectHeader
                detail={detail}
                onCancel={cancelRun}
                onInvokeCodex={invokeCodex}
                codexBusy={codexBusy}
                codexAvailable={agents.some((agent) => agent.provider === "codex" && agent.status === "available")}
              />
              <nav className="mt-7 flex max-w-full gap-1 overflow-x-auto rounded-xl border border-border bg-white p-1 shadow-sm">
                {([
                  ["overview", Activity, "Run"],
                  ["agents", Bot, "Agent runs"],
                  ["graph", Network, "Knowledge graph"],
                  ["notes", BookOpen, "Course notes"],
                  ["reviews", ShieldCheck, `Evidence audit${detail.reviews.filter((item) => item.status === "open").length ? ` (${detail.reviews.filter((item) => item.status === "open").length})` : ""}`],
                  ["sources", FileSearch, "Sources"],
                ] as const).map(([value, Icon, label]) => (
                  <button
                    key={value}
                    onClick={() => setTab(value)}
                    className={cn("flex min-w-fit items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition", tab === value ? "bg-[#17231f] text-white" : "text-slate-500 hover:bg-muted hover:text-slate-800")}
                  >
                    <Icon size={16} /> {label}
                  </button>
                ))}
              </nav>
              <section className="mt-6">
                {tab === "overview" && <Overview detail={detail} agents={agents} />}
                {tab === "agents" && detail.run && <AgentRuns runId={detail.run.id} tasks={detail.tasks} />}
                {tab === "graph" && <GraphView graph={detail.graph} />}
                {tab === "notes" && <Notes detail={detail} refresh={() => loadDetail(detail.project.id)} />}
                {tab === "reviews" && <ReviewWorkbench projectId={detail.project.id} refreshProject={() => loadDetail(detail.project.id)} />}
                {tab === "sources" && <SourceExplorer detail={detail} />}
              </section>
            </>
          )}
        </div>
      </main>
    </div>
  );
}

function Welcome({ agents }: { agents: Agent[] }) {
  return (
    <div className="grid min-h-[70vh] place-items-center">
      <div className="max-w-2xl text-center">
        <div className="mx-auto grid h-16 w-16 place-items-center rounded-2xl bg-emerald-50 text-primary"><GitFork size={30} /></div>
        <h1 className="mt-6 font-serif text-4xl font-semibold tracking-tight md:text-5xl">Turn curiosity into a cited learning path.</h1>
        <p className="mx-auto mt-5 max-w-xl text-base leading-7 text-slate-500">Name any topic above. Atlas plans bounded tasks, runs internal research and evidence checks, verifies source passages, then grows your notes and knowledge graph without a manual review backlog.</p>
        <div className="mt-8 flex flex-wrap justify-center gap-2">
          {agents.map((agent) => <span key={agent.provider} className="flex items-center gap-2 rounded-full border border-border bg-white px-3 py-2 text-xs font-semibold"><Bot size={14} /> {agent.provider} <Badge value={agent.status} /></span>)}
          {!agents.length && <span className="text-sm text-slate-400">Start the host runner to discover local agents.</span>}
        </div>
      </div>
    </div>
  );
}

function ProjectHeader({ detail, onCancel, onInvokeCodex, codexBusy, codexAvailable }: { detail: ProjectDetail; onCancel: () => void; onInvokeCodex: () => void; codexBusy: boolean; codexAvailable: boolean }) {
  const active = detail.run && ["queued", "running", "reviewing"].includes(detail.run.status);
  return (
    <div className="flex min-w-0 flex-col justify-between gap-5 md:flex-row md:items-end">
      <div className="min-w-0">
        <div className="flex items-center gap-3"><Badge value={detail.run?.status || "draft"} /><span className="text-xs font-semibold uppercase tracking-widest text-slate-400">{detail.project.learner_level}</span></div>
        <h1 className="mt-3 font-serif text-3xl font-semibold tracking-tight md:text-4xl">{detail.project.title}</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">{detail.project.goal}</p>
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        <Button data-testid="research-with-codex" size="sm" onClick={onInvokeCodex} disabled={codexBusy || !codexAvailable} title={codexAvailable ? "Reserve the next task for the local Codex CLI" : "Start the host runner with Codex installed"}>
          {codexBusy ? <LoaderCircle className="animate-spin" size={14} /> : <Bot size={14} />} Research with Codex
        </Button>
        {active && <Button variant="outline" size="sm" onClick={onCancel}><Square size={13} /> Cancel run</Button>}
      </div>
    </div>
  );
}

function Overview({ detail, agents }: { detail: ProjectDetail; agents: Agent[] }) {
  const completed = detail.tasks.filter((task) => task.status === "completed").length;
  const progress = detail.run ? Math.min(100, Math.round((completed / Math.max(detail.run.task_budget, 1)) * 100)) : 0;
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Metric icon={Activity} label="Run progress" value={`${progress}%`} note={`${completed} of ${detail.run?.task_budget || 0} task slots complete`} />
        <Metric icon={Network} label="Knowledge graph" value={String(detail.graph.nodes.length)} note={`${detail.graph.edges.length} supported relationships`} />
        <Metric icon={FileSearch} label="Source record" value={String(detail.sources.length)} note={`${detail.sources.filter((item) => item.status === "fetched").length} fetched successfully`} />
        <Metric icon={ShieldCheck} label="Manual review" value={String(detail.reviews.filter((item) => item.status === "open").length)} note="Automatic evidence policy handles routine findings" />
      </div>

      <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(280px,340px)]">
        <Card className="min-w-0 overflow-hidden">
          <CardHeader className="flex-row items-center justify-between">
            <div><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Agentic workflow</p><CardTitle className="mt-1">Task board</CardTitle></div>
            {detail.run && <Badge value={detail.run.status} />}
          </CardHeader>
          <CardContent>
            <div className="mb-5 h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progress}%` }} /></div>
            <div className="space-y-3">
              {detail.tasks.map((task, index) => <TaskRow key={task.id} task={task} index={index + 1} />)}
              {!detail.tasks.length && <EmptyLine text="No tasks created yet." />}
            </div>
          </CardContent>
        </Card>

        <div className="min-w-0 space-y-6">
          <Card>
            <CardHeader><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Local workers</p><CardTitle>Agent status</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              {agents.map((agent) => (
                <div key={agent.provider} className="flex items-center justify-between rounded-xl bg-slate-50 px-3 py-3">
                  <div className="flex items-center gap-3"><div className="grid h-8 w-8 place-items-center rounded-lg bg-white text-slate-600 shadow-sm"><Bot size={16} /></div><div><p className="text-sm font-semibold capitalize">{agent.provider}</p><p className="max-w-[150px] truncate text-[11px] text-slate-400">{agent.mode.replaceAll("_", " ")}</p></div></div>
                  <Badge value={agent.status} />
                </div>
              ))}
              {!agents.length && <EmptyLine text="Host runner is offline." />}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Recent activity</p><CardTitle>Research log</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              {detail.events.slice(-6).reverse().map((event) => (
                <div key={event.id} className="flex gap-3"><Clock3 className="mt-0.5 shrink-0 text-slate-300" size={15} /><div><p className="text-xs leading-5 text-slate-600">{event.message}</p><p className="mt-0.5 text-[10px] text-slate-400">{new Date(event.created_at).toLocaleTimeString()}</p></div></div>
              ))}
              {!detail.events.length && <EmptyLine text="No activity recorded yet." />}
            </CardContent>
          </Card>
        </div>
      </div>
      {detail.submissions.length > 0 && <SubmissionComparison detail={detail} />}
    </div>
  );
}

function Metric({ icon: Icon, label, value, note }: { icon: typeof Activity; label: string; value: string; note: string }) {
  return <Card className="overflow-hidden"><CardContent className="p-5"><div className="flex items-start justify-between"><p className="text-xs font-bold uppercase tracking-widest text-slate-400">{label}</p><Icon size={17} className="text-primary" /></div><p className="mt-4 font-serif text-3xl font-semibold">{value}</p><p className="mt-1 text-xs leading-5 text-slate-400">{note}</p></CardContent></Card>;
}

function TaskRow({ task, index }: { task: Task; index: number }) {
  const Icon = task.status === "completed" ? Check : task.status === "running" || task.status === "leased" ? LoaderCircle : Clock3;
  return (
    <div className="flex min-w-0 items-start gap-3 overflow-hidden rounded-xl border border-border px-3 py-3.5 sm:items-center">
      <div className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-lg text-xs font-bold", task.status === "completed" ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500")}><Icon size={15} className={cn(task.status === "running" && "animate-spin")} /></div>
      <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">{index}. {task.role}</p>{task.provider && <span className="text-[10px] font-semibold capitalize text-primary">{task.provider}</span>}</div><p className="mt-1 break-words text-sm font-medium leading-5 text-slate-700">{task.objective}</p></div>
      <Badge value={task.status} className="shrink-0" />
    </div>
  );
}

function SubmissionComparison({ detail }: { detail: ProjectDetail }) {
  const author = [...detail.submissions].reverse().find((item) => item.kind === "result");
  const review = [...detail.submissions].reverse().find((item) => item.kind === "review");
  if (!author) return null;
  return (
    <Card>
      <CardHeader><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Cross-agent audit</p><CardTitle>Latest author and reviewer results</CardTitle></CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        <ResultPanel title={`${author.provider} · author`} status={author.validation_status} body={String(author.payload.summary || "No summary")}/>
        {review ? <ResultPanel title={`${review.provider} · reviewer`} status={review.same_provider_review ? "same provider" : "cross provider"} body={String(review.payload.summary || "No summary")} warning={review.same_provider_review} /> : <div className="grid min-h-36 place-items-center rounded-xl border border-dashed border-slate-300 text-sm text-slate-400">Review is queued after research tasks finish.</div>}
      </CardContent>
    </Card>
  );
}

function ResultPanel({ title, status, body, warning }: { title: string; status: string; body: string; warning?: boolean }) {
  return <div className={cn("rounded-xl border p-4", warning ? "border-amber-200 bg-amber-50" : "border-border bg-slate-50")}><div className="flex items-center justify-between"><p className="text-xs font-bold uppercase tracking-widest text-slate-500">{title}</p><Badge value={status} /></div><p className="mt-4 text-sm leading-6 text-slate-600">{body}</p>{warning && <p className="mt-3 text-xs font-semibold text-amber-700">A different provider was unavailable; this review used a clean session from the authoring provider.</p>}</div>;
}

function Notes({ detail, refresh }: { detail: ProjectDetail; refresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<{ markdown: string; sections: unknown[] } | null>(null);
  useEffect(() => {
    const loadDraft = () => api<{ markdown: string; sections: unknown[] }>(`/api/v1/projects/${detail.project.id}/draft`).then(setDraft).catch(() => {});
    loadDraft();
    const timer = setInterval(loadDraft, 3500);
    return () => clearInterval(timer);
  }, [detail.project.id]);
  async function regenerate() {
    setBusy(true);
    try { await api(`/api/v1/projects/${detail.project.id}/course/regenerate`, { method: "POST" }); await refresh(); } finally { setBusy(false); }
  }
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between border-b border-border">
        <div><p className="text-xs font-bold uppercase tracking-widest text-slate-400">{detail.course ? `Version ${detail.course.version}` : "Live provisional draft"}</p><CardTitle className="mt-1">Course notebook</CardTitle></div>
        <div className="flex flex-wrap gap-2"><Link href={`/projects/${detail.project.id}/docs`}><Button size="sm"><BookOpen size={14} /> Open documentation</Button></Link><Button variant="outline" size="sm" onClick={regenerate} disabled={busy}>{busy ? <LoaderCircle className="animate-spin" size={14} /> : <RefreshCw size={14} />} Regenerate</Button>{detail.course && <a href={`${API_URL}/api/v1/projects/${detail.project.id}/export.md`} download><Button variant="outline" size="sm"><Download size={14} /> Legacy Markdown</Button></a>}</div>
      </CardHeader>
      <CardContent className="px-6 py-8 md:px-12">
        {detail.course ? <article className="prose-atlas mx-auto max-w-3xl"><ReactMarkdown>{detail.course.markdown}</ReactMarkdown></article> : draft?.markdown ? <div><div className="mx-auto mb-6 max-w-3xl rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800"><strong>Live draft:</strong> sections appear as agents complete them. Atlas automatically verifies quotations and excludes unsupported claims before publishing.</div><article className="prose-atlas mx-auto max-w-3xl"><ReactMarkdown>{draft.markdown}</ReactMarkdown></article></div> : <div className="grid min-h-[420px] place-items-center text-center"><div><BookOpen className="mx-auto text-slate-300" size={38} /><p className="mt-4 font-serif text-2xl font-semibold">Waiting for the first research section</p><p className="mt-2 max-w-md text-sm leading-6 text-slate-500">Completed agent sections will appear here immediately as provisional notes.</p></div></div>}
      </CardContent>
    </Card>
  );
}

function Reviews({ detail, refresh }: { detail: ProjectDetail; refresh: () => Promise<void> }) {
  async function decide(id: string, decision: string) {
    await api(`/api/v1/review-items/${id}`, { method: "PATCH", body: JSON.stringify({ decision }) });
    await refresh();
  }
  return (
    <div className="space-y-3">
      {detail.reviews.map((item) => (
        <Card key={item.id} className={cn(item.status === "open" ? "border-amber-200" : "opacity-70")}>
          <CardContent className="flex flex-col gap-4 p-5 md:flex-row md:items-center">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-amber-50 text-amber-700"><CircleAlert size={18} /></div>
            <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><Badge value={item.status} /><span className="text-[11px] font-bold uppercase tracking-widest text-slate-400">{item.category.replaceAll("_", " ")}</span></div><p className="mt-2 text-sm leading-6 text-slate-600">{item.message}</p></div>
            {item.status === "open" && <div className="flex shrink-0 gap-2"><Button size="sm" variant="outline" onClick={() => decide(item.id, "reject")}>Reject</Button><Button size="sm" variant="outline" onClick={() => decide(item.id, "research_further")}>Research more</Button><Button size="sm" onClick={() => decide(item.id, "accept_as_user_authored")}>Accept as mine</Button></div>}
          </CardContent>
        </Card>
      ))}
      {!detail.reviews.length && <Card><CardContent className="grid min-h-[380px] place-items-center text-center"><div><ShieldCheck className="mx-auto text-emerald-500" size={38} /><p className="mt-4 font-serif text-2xl font-semibold">No manual review needed</p><p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-slate-500">Verified findings are used automatically. Unsupported or contradictory interpretations are excluded from factual pages and retained in the audit trail.</p></div></CardContent></Card>}
    </div>
  );
}

function Sources({ detail }: { detail: ProjectDetail }) {
  return <Card><CardHeader><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Provenance ledger</p><CardTitle>{detail.sources.length} discovered sources</CardTitle></CardHeader><CardContent className="space-y-2">{detail.sources.map((source) => <a key={source.id} href={source.url} target="_blank" rel="noreferrer" className="flex items-center gap-3 rounded-xl border border-border px-4 py-3 transition hover:border-primary/30 hover:bg-emerald-50/30"><div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500"><FileSearch size={16} /></div><div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold text-slate-700">{source.title || source.url}</p><p className="mt-0.5 truncate text-xs text-slate-400">{source.url}</p></div><Badge value={source.status} /><ArrowUpRight size={15} className="text-slate-300" /></a>)}{!detail.sources.length && <EmptyLine text="Sources discovered by Brave or an agent will appear here." />}</CardContent></Card>;
}

function EmptyLine({ text }: { text: string }) {
  return <div className="rounded-xl border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-400">{text}</div>;
}
