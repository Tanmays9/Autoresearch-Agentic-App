"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Ban, Clipboard, Download, Pause, Play, RefreshCw, Search, TerminalSquare } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { API_URL, api } from "@/lib/api";
import type { AgentExecution, ExecutionEvent, Task } from "@/lib/types";
import { cn } from "@/lib/utils";

type LogView = "raw" | "events" | "result" | "errors";
type AgentSettings = {
  provider_mode: "inhouse_azure" | "codex";
  inhouse_agent_concurrency: number;
  inhouse_tool_rounds: number;
  default_task_budget: number;
  documentation_experiment_budget: number;
  azure_token_budget: number;
  azure_cost_budget_usd: number;
  codex_fallback: boolean;
  azure: { ready: boolean; research_deployment: string; reasoning_deployment: string; api_version: string };
  brave_ready: boolean;
};

export function AgentRuns({ runId, tasks }: { runId: string; tasks: Task[] }) {
  const [executions, setExecutions] = useState<AgentExecution[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<AgentExecution | null>(null);
  const [events, setEvents] = useState<ExecutionEvent[]>([]);
  const [view, setView] = useState<LogView>("raw");
  const [query, setQuery] = useState("");
  const [paused, setPaused] = useState(false);
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<{ codex_concurrency: number; codex_web_research: boolean }>({ codex_concurrency: 3, codex_web_research: true });
  const [agentSettings, setAgentSettings] = useState<AgentSettings | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  async function loadExecutions() {
    const value = await api<{ executions: AgentExecution[] }>(`/api/v1/research-runs/${runId}/executions`);
    setExecutions(value.executions);
    setSelectedId((current) => current ?? value.executions.find((item) => ["running", "cancel_requested"].includes(item.status))?.id ?? value.executions[0]?.id ?? null);
  }

  useEffect(() => {
    loadExecutions().catch(() => {});
    api<{ codex_concurrency: number; codex_web_research: boolean }>("/api/v1/settings/research").then(setSettings).catch(() => {});
    api<AgentSettings>("/api/v1/settings/agents").then(setAgentSettings).catch(() => {});
    const timer = setInterval(() => loadExecutions().catch(() => {}), 2000);
    return () => clearInterval(timer);
  }, [runId]);

  async function updateConcurrency(value: number) {
    const updated = await api<{ codex_concurrency: number; codex_web_research: boolean }>("/api/v1/settings/research", { method: "PATCH", body: JSON.stringify({ codex_concurrency: value }) });
    setSettings(updated);
  }
  async function updateWebResearch(value: boolean) {
    const updated = await api<{ codex_concurrency: number; codex_web_research: boolean }>("/api/v1/settings/research", { method: "PATCH", body: JSON.stringify({ codex_web_research: value }) });
    setSettings(updated);
  }
  async function updateAgentSettings(patch: Partial<AgentSettings>) {
    const updated = await api<AgentSettings>("/api/v1/settings/agents", { method: "PATCH", body: JSON.stringify(patch) });
    setAgentSettings(updated);
  }

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      setEvents([]);
      return;
    }
    let open = true;
    api<AgentExecution>(`/api/v1/executions/${selectedId}`).then((value) => {
      if (!open) return;
      setSelected(value);
      setEvents(value.events || []);
    }).catch(() => {});
    const stream = new EventSource(`${API_URL}/api/v1/executions/${selectedId}/events`);
    stream.addEventListener("log", (message) => {
      const incoming = JSON.parse((message as MessageEvent).data) as ExecutionEvent;
      setEvents((current) => {
        const bySequence = new Map(current.map((item) => [item.sequence, item]));
        bySequence.set(incoming.sequence, incoming);
        return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
      });
    });
    return () => {
      open = false;
      stream.close();
    };
  }, [selectedId]);

  useEffect(() => {
    if (!paused && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [events, paused, view]);

  const visibleEvents = useMemo(() => {
    const lower = query.trim().toLowerCase();
    const base = view === "errors"
      ? events.filter((item) => item.stream === "stderr" || /error|fail|diagnostic/.test(item.event_type.toLowerCase()))
      : events;
    return lower ? base.filter((item) => `${item.event_type} ${item.content}`.toLowerCase().includes(lower)) : base;
  }, [events, query, view]);

  const rawText = view === "result"
    ? JSON.stringify(selected?.result || {}, null, 2)
    : visibleEvents.map((item) => view === "events" ? `[${item.sequence}] ${item.event_type} · ${item.stream}` : `[${item.stream}] ${item.content}`).join("\n");

  async function cancel() {
    if (!selected) return;
    setBusy(true);
    try {
      await api(`/api/v1/executions/${selected.id}/cancel`, { method: "POST" });
      await loadExecutions();
    } finally { setBusy(false); }
  }

  async function retry() {
    if (!selected) return;
    setBusy(true);
    try {
      await api(`/api/v1/tasks/${selected.task_id}/retry`, { method: "POST" });
      await loadExecutions();
    } finally { setBusy(false); }
  }

  function download() {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([rawText], { type: "text/plain" }));
    link.download = `atlas-execution-${selected?.id || "log"}.txt`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  return (
    <div className="grid min-w-0 gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
      <Card className="min-w-0 overflow-hidden">
        <CardHeader className="border-b"><div className="flex items-start justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Local agent runtime</p><CardTitle>Execution history</CardTitle></div><label className="text-right text-[10px] font-bold uppercase tracking-widest text-slate-400">Parallel workers<select aria-label="Parallel research workers" value={agentSettings?.provider_mode === "inhouse_azure" ? agentSettings.inhouse_agent_concurrency : settings.codex_concurrency} onChange={(event) => agentSettings?.provider_mode === "inhouse_azure" ? updateAgentSettings({ inhouse_agent_concurrency: Number(event.target.value) }) : updateConcurrency(Number(event.target.value))} className="mt-1 block rounded-lg border bg-white px-2 py-1.5 text-xs font-semibold text-slate-700">{[1,2,3,4,5].map((value) => <option key={value} value={value}>{value}</option>)}</select></label></div>{agentSettings && <div className="mt-2 rounded-xl border border-border bg-slate-50 p-3"><div className="flex flex-wrap items-center gap-2"><Badge value={agentSettings.azure.ready ? "azure ready" : "authentication required"} /><Badge value={agentSettings.brave_ready ? "brave ready" : "brave not configured"} /></div><label className="mt-3 flex items-center justify-between gap-3 text-xs font-semibold text-slate-600">Default worker<select aria-label="Default research provider" value={agentSettings.provider_mode} onChange={(event) => updateAgentSettings({ provider_mode: event.target.value as AgentSettings["provider_mode"] })} className="rounded-lg border bg-white px-2 py-1.5"><option value="inhouse_azure">In-house LangGraph</option><option value="codex">Codex CLI</option></select></label><p className="mt-2 text-[11px] leading-5 text-slate-500">Researchers: {agentSettings.azure.research_deployment} · Planner/reviewer: {agentSettings.azure.reasoning_deployment}</p></div>}{agentSettings?.provider_mode === "codex" && <label className="mt-2 flex items-center gap-2 text-xs font-semibold text-emerald-700"><input type="checkbox" checked={settings.codex_web_research} onChange={(event) => updateWebResearch(event.target.checked)} /> Codex web research · read-only sandbox</label>}</CardHeader>
        <CardContent className="max-h-[720px] space-y-2 overflow-y-auto p-3">
          {executions.map((execution) => (
            <button key={execution.id} onClick={() => setSelectedId(execution.id)} className={cn("w-full rounded-xl border p-3 text-left transition", selectedId === execution.id ? "border-primary bg-emerald-50/60" : "border-border hover:bg-slate-50")}>
              <div className="flex items-center justify-between gap-2"><span className="text-[10px] font-bold uppercase tracking-widest text-primary">{execution.task?.role || "task"}</span><Badge value={execution.status} /></div>
              <p className="mt-2 line-clamp-2 text-sm font-semibold leading-5 text-slate-700">{execution.task?.objective}</p>
              <p className="mt-2 text-[11px] text-slate-400">Attempt {execution.task?.attempts || 1} · {new Date(execution.started_at).toLocaleTimeString()}</p>
            </button>
          ))}
          {tasks.filter((task) => !executions.some((execution) => execution.task_id === task.id)).map((task) => (
            <div key={task.id} className="rounded-xl border border-dashed p-3 opacity-70"><div className="flex justify-between"><span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">{task.role}</span><Badge value={task.status} /></div><p className="mt-2 text-sm text-slate-600">{task.objective}</p></div>
          ))}
          {!executions.length && !tasks.length && <p className="p-6 text-center text-sm text-slate-400">No agent tasks yet.</p>}
        </CardContent>
      </Card>

      <Card className="min-w-0 overflow-hidden">
        <CardHeader className="gap-4 border-b lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0"><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Live execution preview</p><CardTitle className="mt-1 truncate">{selected?.task?.objective || "Select an execution"}</CardTitle></div>
          {selected && <div className="flex flex-wrap gap-2"><Badge value={selected.status} />{["running", "cancel_requested"].includes(selected.status) && <Button size="sm" variant="danger" onClick={cancel} disabled={busy}><Ban size={14} /> Cancel</Button>}{["failed", "cancelled"].includes(selected.status) && <Button size="sm" onClick={retry} disabled={busy}><RefreshCw size={14} /> Retry</Button>}</div>}
        </CardHeader>
        <CardContent className="p-0">
          <div className="flex flex-col gap-3 border-b bg-slate-50 p-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-wrap gap-1">{(["raw", "events", "result", "errors"] as LogView[]).map((item) => <Button key={item} size="sm" variant={view === item ? "default" : "ghost"} onClick={() => setView(item)}>{item}</Button>)}</div>
            <div className="flex flex-wrap gap-2">
              <div className="relative"><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={14}/><Input aria-label="Search execution logs" value={query} onChange={(event) => setQuery(event.target.value)} className="h-8 w-52 pl-8 text-xs" placeholder="Search logs" /></div>
              <Button size="sm" variant="outline" onClick={() => setPaused((value) => !value)}>{paused ? <Play size={13}/> : <Pause size={13}/>} {paused ? "Resume" : "Pause"}</Button>
              <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(rawText)}><Clipboard size={13}/> Copy</Button>
              <Button size="sm" variant="outline" onClick={download}><Download size={13}/> Log</Button>
            </div>
          </div>
          {selected ? <pre ref={logRef} tabIndex={0} aria-label="Sanitized agent execution log" className="h-[560px] max-w-full overflow-auto whitespace-pre-wrap break-words bg-[#101815] p-5 font-mono text-xs leading-6 text-emerald-100 outline-none">{rawText || "Waiting for agent output…"}</pre> : <div className="grid h-[560px] place-items-center text-center"><div><TerminalSquare className="mx-auto text-slate-300" size={40}/><p className="mt-3 text-sm text-slate-500">Choose an execution to monitor it from queue to result.</p></div></div>}
          {selected && <div className="grid gap-3 border-t p-4 text-xs text-slate-500 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6"><span><strong>Status:</strong> {selected.status}</span><span><strong>Runner:</strong> {selected.runner_id}</span><span><strong>Model:</strong> {selected.model || selected.provider}</span><span><strong>Tokens:</strong> {(selected.input_tokens || 0) + (selected.output_tokens || 0)}</span><span><strong>Est. cost:</strong> ${(selected.cost_usd || 0).toFixed(4)}</span><span><strong>Exit:</strong> {selected.exit_code ?? "—"}</span></div>}
        </CardContent>
      </Card>
    </div>
  );
}
