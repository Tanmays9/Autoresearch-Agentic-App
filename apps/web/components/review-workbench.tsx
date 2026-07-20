"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowUpRight, CheckCircle2, FileWarning, Search } from "lucide-react";
import ReactMarkdown from "react-markdown";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { ReviewDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

export function ReviewWorkbench({ projectId, refreshProject }: { projectId: string; refreshProject: () => Promise<void> }) {
  const [items, setItems] = useState<ReviewDetail[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    const first = await api<{ items: ReviewDetail[]; total: number; page_size: number }>(`/api/v1/projects/${projectId}/review-items?page_size=100`);
    const pageCount = Math.ceil(first.total / first.page_size);
    const remaining = pageCount > 1
      ? await Promise.all(Array.from({ length: pageCount - 1 }, (_, index) => api<{ items: ReviewDetail[] }>(`/api/v1/projects/${projectId}/review-items?page=${index + 2}&page_size=100`)))
      : [];
    const allItems = [first.items, ...remaining.map((value) => value.items)].flat();
    setItems(allItems);
    setSelectedKey((current) => current ?? allItems[0]?.claim?.id ?? allItems[0]?.id ?? null);
  }

  useEffect(() => { load().catch(() => {}); }, [projectId]);

  const groups = useMemo(() => {
    const grouped = new Map<string, ReviewDetail[]>();
    for (const item of items) {
      const key = item.claim?.id || item.id;
      grouped.set(key, [...(grouped.get(key) || []), item]);
    }
    const lower = query.trim().toLowerCase();
    return [...grouped.entries()].filter(([, values]) => !lower || `${values[0].claim?.text || ""} ${values.map((item) => item.message).join(" ")}`.toLowerCase().includes(lower));
  }, [items, query]);
  const selectedGroup = groups.find(([key]) => key === selectedKey)?.[1] || groups[0]?.[1] || [];
  const selected = selectedGroup[0];

  async function decide(decision: string) {
    if (!selected) return;
    setBusy(true);
    try {
      await api(`/api/v1/review-items/${selected.id}`, { method: "PATCH", body: JSON.stringify({ decision }) });
      await Promise.all([load(), refreshProject()]);
    } finally { setBusy(false); }
  }

  return (
    <div className="grid min-w-0 gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
      <Card className="min-w-0 overflow-hidden">
        <CardHeader className="border-b"><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Evidence decisions</p><CardTitle>Claims needing review</CardTitle><div className="relative mt-3"><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={15}/><Input aria-label="Search review claims" value={query} onChange={(event) => setQuery(event.target.value)} className="pl-9" placeholder="Search claims" /></div></CardHeader>
        <CardContent className="max-h-[760px] space-y-2 overflow-y-auto p-3">
          {groups.map(([key, values]) => {
            const item = values[0];
            return <button key={key} onClick={() => setSelectedKey(key)} className={cn("w-full rounded-xl border p-3 text-left transition", key === (selected?.claim?.id || selected?.id) ? "border-amber-400 bg-amber-50/60" : "border-border hover:bg-slate-50")}><div className="flex items-center justify-between gap-2"><Badge value={item.status}/><span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">{values.length} issue{values.length === 1 ? "" : "s"}</span></div><p className="mt-2 line-clamp-3 text-sm font-semibold leading-5 text-slate-700">{item.claim?.text || item.message}</p><p className="mt-2 text-[11px] uppercase tracking-wide text-amber-700">{[...new Set(values.map((value) => value.category.replaceAll("_", " ")))].join(" · ")}</p></button>;
          })}
          {!groups.length && <div className="p-8 text-center"><CheckCircle2 className="mx-auto text-emerald-500" size={34}/><p className="mt-3 text-sm text-slate-500">Nothing needs review.</p></div>}
        </CardContent>
      </Card>

      {selected ? <div className="min-w-0 space-y-5">
        <Card>
          <CardHeader className="gap-4 border-b lg:flex-row lg:items-start lg:justify-between"><div className="min-w-0"><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Claim and provenance</p><CardTitle className="mt-2 leading-7">{selected.claim?.text || "Validation issue"}</CardTitle><div className="mt-3 flex flex-wrap gap-2"><Badge value={selected.claim?.status || selected.status}/><Badge value={selected.claim?.provenance || selected.category}/>{selected.submission && <span className="text-xs text-slate-500">Authored by {selected.submission.provider} for {selected.task?.role}</span>}</div></div>{selected.status === "open" && <div className="flex shrink-0 flex-wrap gap-2"><Button size="sm" variant="outline" disabled={busy} onClick={() => decide("reject")}>Reject</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => decide("research_further")}>Research more</Button><Button size="sm" disabled={busy} onClick={() => decide("accept_as_user_authored")}>Accept as mine</Button></div>}</CardHeader>
          <CardContent className="grid gap-5 p-5 lg:grid-cols-2">
            <section><h3 className="text-xs font-bold uppercase tracking-widest text-slate-400">Why it was flagged</h3><div className="mt-3 space-y-2">{selectedGroup.map((item) => <div key={item.id} className="flex gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3"><AlertTriangle className="mt-0.5 shrink-0 text-amber-700" size={16}/><div><p className="text-xs font-bold uppercase tracking-wide text-amber-800">{item.category.replaceAll("_", " ")}</p><p className="mt-1 text-sm leading-6 text-slate-700">{item.message}</p></div></div>)}</div></section>
            <section><h3 className="text-xs font-bold uppercase tracking-widest text-slate-400">Originating task</h3><div className="mt-3 rounded-xl bg-slate-50 p-4"><p className="text-sm font-semibold text-slate-700">{selected.task?.objective || "Unknown task"}</p><p className="mt-2 text-xs text-slate-500">Execution {selected.execution?.id || "—"} · {selected.execution?.status || "—"}</p></div>{(selected.concepts.length > 0 || selected.relationships.length > 0) && <div className="mt-4"><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Graph impact</p><p className="mt-2 text-sm text-slate-600">{selected.concepts.map((item) => item.name).join(", ") || "No concepts"}{selected.relationships.map((item) => ` · ${item.source} ${item.type} ${item.target}`)}</p></div>}</section>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="border-b"><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Submitted evidence</p><CardTitle>{selected.evidence.length} quotation{selected.evidence.length === 1 ? "" : "s"}</CardTitle></CardHeader>
          <CardContent className="space-y-3 p-5">{selected.evidence.map((evidence) => <div key={evidence.id} className={cn("rounded-xl border p-4", evidence.verified ? "border-emerald-200 bg-emerald-50/40" : "border-red-200 bg-red-50/40")}><div className="flex flex-wrap items-center justify-between gap-2"><Badge value={evidence.verified ? "supported" : "failed"}/>{evidence.source && <a href={evidence.source.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs font-semibold text-primary">{evidence.source.title || evidence.source.url}<ArrowUpRight size={13}/></a>}</div><blockquote className="mt-3 border-l-2 border-slate-300 pl-3 text-sm italic leading-6 text-slate-700">“{evidence.quote}”</blockquote>{evidence.locator && <p className="mt-2 text-xs text-slate-500">Locator: {evidence.locator}</p>}{evidence.error && <p className="mt-2 flex items-center gap-2 text-xs font-semibold text-red-700"><FileWarning size={14}/>{evidence.error}</p>}</div>)}{!selected.evidence.length && <p className="rounded-xl border border-dashed p-6 text-center text-sm text-slate-400">No quotation was submitted for this claim.</p>}</CardContent>
        </Card>

        {selected.submission?.note_section_markdown && <Card><CardHeader className="border-b"><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Provisional note section</p><CardTitle>{selected.submission.summary}</CardTitle></CardHeader><CardContent className="p-6"><article className="prose-atlas max-w-none"><ReactMarkdown>{selected.submission.note_section_markdown}</ReactMarkdown></article></CardContent></Card>}
      </div> : <Card><CardContent className="grid min-h-[560px] place-items-center text-center text-sm text-slate-400">Select a claim to inspect its evidence and draft.</CardContent></Card>}
    </div>
  );
}
