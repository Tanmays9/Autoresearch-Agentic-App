"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, FileSearch, LoaderCircle, Pause, Play, Search, Square, Telescope } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { CrawlJob, ProjectDetail } from "@/lib/types";

type SourceDetail = { id: string; url: string; title?: string; status: string; content_type?: string; http_status?: number; extraction_error?: string; content: string; chunks: Array<{ index: number; content: string }> };

export function SourceExplorer({ detail }: { detail: ProjectDetail }) {
  const [crawl, setCrawl] = useState<CrawlJob | null>(null);
  const [selected, setSelected] = useState<SourceDetail | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadCrawl() {
    if (!detail.run) return;
    const value = await api<CrawlJob>(`/api/v1/research-runs/${detail.run.id}/crawl`);
    setCrawl(value);
  }
  useEffect(() => {
    loadCrawl().catch(() => setCrawl(null));
    const timer = setInterval(() => loadCrawl().catch(() => {}), 3000);
    return () => clearInterval(timer);
  }, [detail.run?.id]);

  const sources = useMemo(() => {
    const lower = query.trim().toLowerCase();
    return lower ? detail.sources.filter((source) => `${source.title || ""} ${source.url}`.toLowerCase().includes(lower)) : detail.sources;
  }, [detail.sources, query]);
  const progress = crawl ? Math.min(100, Math.round((crawl.processed_count / Math.max(1, crawl.max_pages)) * 100)) : 0;

  async function discover() {
    setBusy(true);
    try { await api(`/api/v1/projects/${detail.project.id}/source-discovery`, { method: "POST" }); await loadCrawl(); }
    finally { setBusy(false); }
  }
  async function control(action: string) {
    if (!crawl) return;
    setBusy(true);
    try { await api(`/api/v1/crawl-jobs/${crawl.id}/${action}`, { method: "POST" }); await loadCrawl(); }
    finally { setBusy(false); }
  }
  async function preview(sourceId: string) {
    setSelected(await api<SourceDetail>(`/api/v1/sources/${sourceId}`));
  }

  return <div className="space-y-5">
    <Card>
      <CardHeader className="gap-4 border-b lg:flex-row lg:items-center lg:justify-between"><div><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Topic-wide crawler</p><CardTitle className="mt-1">Web discovery and extraction</CardTitle></div><div className="flex flex-wrap gap-2"><Button size="sm" onClick={discover} disabled={busy}>{busy ? <LoaderCircle className="animate-spin" size={14}/> : <Telescope size={14}/>} Discover sources</Button>{crawl?.status === "running" && <Button size="sm" variant="outline" onClick={() => control("pause")}><Pause size={14}/> Pause</Button>}{crawl?.status === "paused" && <Button size="sm" variant="outline" onClick={() => control("resume")}><Play size={14}/> Resume</Button>}{crawl && !["completed", "cancelled"].includes(crawl.status) && <Button size="sm" variant="danger" onClick={() => control("cancel")}><Square size={13}/> Cancel</Button>}</div></CardHeader>
      <CardContent className="p-5">{crawl ? <><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex flex-wrap gap-2"><Badge value={crawl.status}/><span className="text-xs text-slate-500">{crawl.processed_count} processed · {crawl.fetched_count} fetched · {crawl.failed_count} failed · {crawl.skipped_count} skipped</span></div><span className="text-sm font-semibold text-slate-700">{progress}% of {crawl.max_pages}-page limit</span></div><div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progress}%` }}/></div>{crawl.error && <p className="mt-3 text-xs text-amber-700">{crawl.error}</p>}</> : <p className="text-sm text-slate-500">Discovery will use Brave when configured and Codex web research as a fallback.</p>}</CardContent>
    </Card>

    <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(340px,440px)]">
      <Card className="min-w-0 overflow-hidden"><CardHeader className="gap-3 border-b sm:flex-row sm:items-center sm:justify-between"><div><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Provenance ledger</p><CardTitle>{detail.sources.length} sources</CardTitle></div><div className="relative"><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={15}/><Input aria-label="Search sources" value={query} onChange={(event) => setQuery(event.target.value)} className="w-64 pl-9" placeholder="Search sources"/></div></CardHeader><CardContent className="max-h-[680px] space-y-2 overflow-y-auto p-3">{sources.map((source) => <button key={source.id} onClick={() => preview(source.id)} className="flex w-full min-w-0 items-center gap-3 rounded-xl border border-border px-4 py-3 text-left transition hover:border-primary/30 hover:bg-emerald-50/30"><div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500"><FileSearch size={16}/></div><div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold text-slate-700">{source.title || source.url}</p><p className="mt-0.5 truncate text-xs text-slate-400">{source.url}</p></div><Badge value={source.status}/></button>)}{!sources.length && <p className="p-8 text-center text-sm text-slate-400">No matching sources.</p>}</CardContent></Card>

      <Card className="min-w-0 overflow-hidden"><CardHeader className="border-b"><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Extracted source preview</p><CardTitle className="truncate">{selected?.title || "Select a source"}</CardTitle>{selected && <a href={selected.url} target="_blank" rel="noreferrer" className="mt-2 flex items-center gap-1 truncate text-xs font-semibold text-primary">{selected.url}<ArrowUpRight size={13}/></a>}</CardHeader><CardContent className="p-0">{selected ? <><div className="flex gap-2 border-b p-3"><Badge value={selected.status}/><span className="text-xs text-slate-500">{selected.content_type || "unknown type"} · HTTP {selected.http_status || "—"}</span></div><pre className="h-[590px] overflow-auto whitespace-pre-wrap break-words bg-slate-50 p-4 font-sans text-xs leading-6 text-slate-700">{selected.content || selected.extraction_error || "No extracted text."}</pre></> : <div className="grid h-[640px] place-items-center p-8 text-center text-sm text-slate-400">Choose a fetched source to inspect the text used for citation verification.</div>}</CardContent></Card>
    </div>

    {crawl && crawl.targets.some((target) => target.error) && <Card><CardHeader><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Crawler diagnostics</p><CardTitle>Failed and skipped URLs</CardTitle></CardHeader><CardContent className="space-y-2">{crawl.targets.filter((target) => target.error).map((target) => <div key={target.id} className="rounded-xl border p-3"><div className="flex justify-between gap-2"><p className="min-w-0 truncate text-sm font-semibold text-slate-700">{target.url}</p><Badge value={target.status}/></div><p className="mt-1 text-xs text-slate-500">{target.error}</p></div>)}</CardContent></Card>}
  </div>;
}
