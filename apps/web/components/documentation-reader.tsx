"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  CircleAlert,
  Download,
  FileDiff,
  FileText,
  Home,
  Library,
  LoaderCircle,
  Menu,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { API_URL, api } from "@/lib/api";
import type { CourseExpansionRequest, CoursePage, CoursePageSummary, CourseRelease, DocumentationRun } from "@/lib/types";
import { cn } from "@/lib/utils";

type TreeResponse = CourseRelease & { pages: CoursePageSummary[] };
type DiffResponse = {
  base: CourseRelease;
  candidate: CourseRelease;
  pages: Array<{ slug: string; title: string; before_score: number; after_score: number; diff: string }>;
};

export function DocumentationReader({ projectId, initialSlug }: { projectId: string; initialSlug: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedVersion = searchParams.get("version") || "latest";
  const [releases, setReleases] = useState<CourseRelease[]>([]);
  const [tree, setTree] = useState<TreeResponse | null>(null);
  const [page, setPage] = useState<CoursePage | null>(null);
  const [documentationRuns, setDocumentationRuns] = useState<DocumentationRun[]>([]);
  const [gapRequests, setGapRequests] = useState<CourseExpansionRequest[]>([]);
  const [gapQuery, setGapQuery] = useState("");
  const [gapSubmitting, setGapSubmitting] = useState(false);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<CoursePageSummary[]>([]);
  const [searching, setSearching] = useState(false);
  const [loading, setLoading] = useState(true);
  const [navOpen, setNavOpen] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadReleases = useCallback(async () => {
    const [releaseData, runData, gapData] = await Promise.all([
      api<{ items: CourseRelease[] }>(`/api/v1/projects/${projectId}/course/releases`),
      api<{ items: DocumentationRun[] }>(`/api/v1/projects/${projectId}/documentation-runs`),
      api<{ items: CourseExpansionRequest[] }>(`/api/v1/projects/${projectId}/course/gap-research`),
    ]);
    setReleases(releaseData.items);
    setDocumentationRuns(runData.items);
    setGapRequests(gapData.items);
  }, [projectId]);

  const loadTreeAndPage = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const value = await api<TreeResponse>(`/api/v1/projects/${projectId}/course/releases/${requestedVersion}/tree`);
      setTree(value);
      const desired = initialSlug || value.pages[0]?.slug;
      if (desired) {
        const selected = await api<CoursePage>(`/api/v1/projects/${projectId}/course/releases/${value.version}/pages/${desired}`);
        setPage(selected);
      } else {
        setPage(null);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load this course");
    } finally {
      setLoading(false);
    }
  }, [initialSlug, projectId, requestedVersion]);

  useEffect(() => {
    Promise.all([loadReleases(), loadTreeAndPage()]).catch((reason) => setError(reason.message));
  }, [loadReleases, loadTreeAndPage]);

  useEffect(() => {
    if (!gapRequests.some((item) => ["queued", "analyzing", "researching"].includes(item.status))) return;
    const timer = setInterval(() => loadReleases().catch(() => {}), 4000);
    return () => clearInterval(timer);
  }, [gapRequests, loadReleases]);

  const activeRun = useMemo(
    () => documentationRuns.find((run) => run.candidate_release_id === tree?.id && run.status === "awaiting_approval"),
    [documentationRuns, tree?.id],
  );

  const progress = useMemo(() => {
    if (!tree || !page) return 0;
    const index = tree.pages.findIndex((item) => item.id === page.id);
    return Math.round(((index + 1) / Math.max(1, tree.pages.length)) * 100);
  }, [page, tree]);

  function navigate(slug: string, version = tree?.version) {
    setNavOpen(false);
    setQuery("");
    setSearchResults([]);
    router.push(`/projects/${projectId}/docs/${slug}${version ? `?version=${version}` : ""}`);
  }

  function selectVersion(version: string) {
    const target = initialSlug || tree?.pages[0]?.slug || "";
    router.push(`/projects/${projectId}/docs/${target}?version=${version}`);
  }

  async function searchCourse(event: FormEvent) {
    event.preventDefault();
    if (!query.trim() || !tree) return;
    setSearching(true);
    try {
      const result = await api<{ items: CoursePageSummary[] }>(
        `/api/v1/projects/${projectId}/course/search?q=${encodeURIComponent(query.trim())}&release_id=${tree.id}`,
      );
      setSearchResults(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Search failed");
    } finally {
      setSearching(false);
    }
  }

  async function openDiff() {
    if (!tree) return;
    setShowDiff(true);
    if (!diff) {
      try {
        setDiff(await api<DiffResponse>(`/api/v1/projects/${projectId}/course/diff/${tree.version}`));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not load the release comparison");
      }
    }
  }

  async function decide(decision: "approve" | "reject") {
    if (!activeRun) return;
    setDecisionBusy(true);
    setError(null);
    try {
      await api(`/api/v1/documentation-runs/${activeRun.id}/${decision}`, {
        method: "POST",
        body: JSON.stringify({ actor: "local_user" }),
      });
      await loadReleases();
      await loadTreeAndPage();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Could not ${decision} this release`);
    } finally {
      setDecisionBusy(false);
    }
  }

  async function submitGap(event: FormEvent) {
    event.preventDefault();
    const value = gapQuery.trim();
    if (!value) return;
    setGapSubmitting(true);
    setError(null);
    try {
      const request = await api<CourseExpansionRequest>(`/api/v1/projects/${projectId}/course/gap-research`, {
        method: "POST",
        body: JSON.stringify({ query: value, max_topics: 5, source_budget: 100 }),
      });
      setGapRequests((current) => [request, ...current.filter((item) => item.id !== request.id)]);
      setGapQuery("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start missing-topic research");
    } finally {
      setGapSubmitting(false);
    }
  }

  if (loading && !tree) {
    return <div className="grid min-h-dvh place-items-center bg-background"><div className="text-center"><LoaderCircle className="mx-auto animate-spin text-primary" /><p className="mt-3 text-sm text-slate-500">Opening documentation…</p></div></div>;
  }

  return (
    <div className="min-h-dvh w-full max-w-full overflow-x-hidden bg-background">
      <header className="sticky top-0 z-40 border-b border-border bg-white/95 backdrop-blur-xl">
        <div className="flex min-h-16 min-w-0 items-center gap-3 px-3 sm:px-5 lg:px-6">
          <Button variant="outline" size="icon" className="lg:hidden" onClick={() => setNavOpen(true)} aria-label="Open course navigation"><Menu size={18} /></Button>
          <Link href="/" className="flex shrink-0 items-center gap-2 font-serif font-semibold text-slate-900"><span className="grid h-8 w-8 place-items-center rounded-lg bg-[#17231f] text-white"><Library size={16} /></span><span className="hidden sm:inline">Atlas Docs</span></Link>
          <div className="hidden h-6 w-px bg-border sm:block" />
          <p className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-600">{tree?.title || "Course documentation"}</p>
          <label className="hidden items-center gap-2 text-xs font-semibold text-slate-500 md:flex">
            <span>Version</span>
            <select aria-label="Course release" value={tree?.version || ""} onChange={(event) => selectVersion(event.target.value)} className="h-9 rounded-lg border border-border bg-white px-3 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary/30">
              {releases.map((release) => <option key={release.id} value={release.version}>v{release.version} · {release.status.replaceAll("_", " ")}</option>)}
            </select>
          </label>
          {tree && <a className="hidden sm:block" href={`${API_URL}/api/v1/projects/${projectId}/course/releases/${tree.version}/export.zip`} download><Button variant="outline" size="sm"><Download size={14} /><span className="hidden xl:inline">Export ZIP</span></Button></a>}
        </div>
      </header>

      {error && <div role="alert" className="mx-3 mt-3 flex items-start justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 sm:mx-5 lg:mx-6"><span className="flex items-start gap-2"><CircleAlert size={16} className="mt-0.5 shrink-0" />{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error"><X size={16} /></button></div>}

      {activeRun && (
        <div className="mx-3 mt-3 flex flex-col gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 sm:mx-5 sm:flex-row sm:items-center lg:mx-6">
          <div className="flex min-w-0 flex-1 items-start gap-3"><ShieldCheck className="mt-0.5 shrink-0 text-amber-700" size={18} /><div><p className="text-sm font-semibold text-amber-950">Documentation improvement is ready for your approval</p><p className="mt-1 text-xs leading-5 text-amber-800">{activeRun.experiments.filter((item) => item.status === "kept").length} non-regressing experiments passed the five-point improvement gate. Publishing never happens automatically.</p></div></div>
          <div className="flex shrink-0 flex-wrap gap-2"><Button variant="outline" size="sm" onClick={openDiff}><FileDiff size={14} /> Review changes</Button><Button variant="outline" size="sm" onClick={() => decide("reject")} disabled={decisionBusy}>Discard</Button><Button size="sm" onClick={() => decide("approve")} disabled={decisionBusy}>{decisionBusy ? <LoaderCircle className="animate-spin" size={14} /> : <Check size={14} />} Publish</Button></div>
        </div>
      )}

      <div className="grid min-w-0 lg:grid-cols-[280px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,1fr)_240px]">
        <CourseSidebar open={navOpen} onClose={() => setNavOpen(false)} projectId={projectId} tree={tree} page={page} query={query} setQuery={setQuery} searching={searching} results={searchResults} onSearch={searchCourse} onNavigate={navigate} onVersion={selectVersion} releases={releases} />

        <main className="min-w-0 px-4 py-6 sm:px-7 lg:px-10 lg:py-10 2xl:px-14">
          <GapResearchPanel requests={gapRequests} query={gapQuery} setQuery={setGapQuery} busy={gapSubmitting} onSubmit={submitGap} />
          {showDiff ? (
            <DiffView diff={diff} onClose={() => setShowDiff(false)} />
          ) : page ? (
            <>
              <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1 overflow-hidden text-xs font-medium text-slate-400"><Link href="/" className="shrink-0 hover:text-primary"><Home size={13} /></Link><ChevronRight size={13} className="shrink-0" /><button onClick={() => navigate(tree?.pages[0]?.slug || "overview")} className="shrink-0 hover:text-primary">Course</button>{page.slug.split("/").map((part, index, parts) => <span key={`${part}-${index}`} className="flex min-w-0 items-center gap-1"><ChevronRight size={13} className="shrink-0" /><span className={cn("truncate capitalize", index === parts.length - 1 && "text-slate-700")}>{part.replaceAll("-", " ")}</span></span>)}</nav>
              <div className="mt-6 flex flex-wrap items-center gap-2"><Badge value={page.status} /><span className="text-xs font-semibold uppercase tracking-widest text-slate-400">{page.page_type.replaceAll("_", " ")}</span><span className="text-xs text-slate-300">·</span><span className="text-xs text-slate-400">Quality {Math.round(page.quality_score)}/100</span></div>
              <article className="prose-atlas mt-2 max-w-4xl overflow-hidden"><ReactMarkdown components={{ a: ({ ...props }) => <a {...props} rel="noreferrer" />, h2: ({ children }) => <h2 id={headingAnchor(children)}>{children}</h2>, h3: ({ children }) => <h3 id={headingAnchor(children)}>{children}</h3>, h4: ({ children }) => <h4 id={headingAnchor(children)}>{children}</h4> }}>{page.markdown}</ReactMarkdown></article>

              {(page.claims.length > 0 || page.sources.length > 0) && <EvidenceFooter page={page} />}

              <div className="mt-12 border-t border-border pt-6">
                <div className="mb-4 flex items-center justify-between text-xs font-semibold text-slate-400"><span>Course progress</span><span>{progress}%</span></div>
                <div className="mb-7 h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progress}%` }} /></div>
                <div className="grid gap-3 sm:grid-cols-2">
                  {page.previous ? <button onClick={() => navigate(page.previous!.slug)} className="group rounded-xl border border-border bg-white p-4 text-left transition hover:border-primary/30 hover:shadow-sm"><span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-slate-400"><ArrowLeft size={14} /> Previous</span><span className="mt-2 block font-serif text-lg font-semibold group-hover:text-primary">{page.previous.title}</span></button> : <div />}
                  {page.next && <button onClick={() => navigate(page.next!.slug)} className="group rounded-xl border border-border bg-white p-4 text-right transition hover:border-primary/30 hover:shadow-sm"><span className="flex items-center justify-end gap-2 text-xs font-semibold uppercase tracking-widest text-slate-400">Next <ArrowRight size={14} /></span><span className="mt-2 block font-serif text-lg font-semibold group-hover:text-primary">{page.next.title}</span></button>}
                </div>
              </div>
            </>
          ) : <EmptyCourse />}
        </main>

        <aside className="hidden border-l border-border bg-white/40 px-5 py-10 xl:block">
          <div className="sticky top-24"><p className="text-[11px] font-bold uppercase tracking-[.16em] text-slate-400">On this page</p><nav className="mt-4 space-y-1" aria-label="Page headings">{page?.headings.map((heading) => <a key={`${heading.anchor}-${heading.level}`} href={`#${heading.anchor}`} className={cn("block border-l-2 border-transparent py-1.5 text-xs leading-5 text-slate-500 hover:border-primary hover:text-primary", heading.level > 2 && "pl-3")}>{heading.title}</a>)}{!page?.headings.length && <p className="text-xs leading-5 text-slate-400">No subheadings on this page.</p>}</nav></div>
        </aside>
      </div>
    </div>
  );
}

function CourseSidebar({ open, onClose, projectId, tree, page, query, setQuery, searching, results, onSearch, onNavigate, onVersion, releases }: { open: boolean; onClose: () => void; projectId: string; tree: TreeResponse | null; page: CoursePage | null; query: string; setQuery: (value: string) => void; searching: boolean; results: CoursePageSummary[]; onSearch: (event: FormEvent) => void; onNavigate: (slug: string) => void; onVersion: (version: string) => void; releases: CourseRelease[] }) {
  const roots = tree?.pages.filter((item) => !item.parent_page_id) || [];
  const children = (parentId: string) => tree?.pages.filter((item) => item.parent_page_id === parentId) || [];
  return <>
    {open && <button className="fixed inset-0 z-40 bg-slate-950/35 lg:hidden" onClick={onClose} aria-label="Close navigation overlay" />}
    <aside className={cn("fixed inset-y-0 left-0 z-50 flex w-[min(88vw,320px)] min-w-0 flex-col border-r border-border bg-white transition-transform lg:sticky lg:top-16 lg:z-20 lg:h-[calc(100dvh-4rem)] lg:w-auto lg:translate-x-0", open ? "translate-x-0" : "-translate-x-full")} aria-label="Course navigation">
      <div className="flex items-center justify-between border-b border-border px-4 py-4 lg:hidden"><p className="font-serif font-semibold">Course index</p><Button variant="outline" size="icon" onClick={onClose} aria-label="Close course navigation"><X size={16} /></Button></div>
      <div className="min-w-0 border-b border-border p-4">
        <form onSubmit={onSearch} className="relative"><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={15} /><Input aria-label="Search this course" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search this course" className="h-10 pl-9 pr-11" /><Button type="submit" variant="ghost" size="icon" aria-label="Search course" disabled={searching || !query.trim()} className="absolute right-1 top-1 h-8 w-8">{searching ? <LoaderCircle className="animate-spin" size={15} /> : <ArrowRight size={15} />}</Button></form>
        {!!results.length && <div className="mt-2 max-h-52 overflow-y-auto rounded-xl border border-border bg-white p-1 shadow-soft">{results.map((result) => <button key={result.id} onClick={() => onNavigate(result.slug)} className="w-full rounded-lg px-3 py-2 text-left hover:bg-muted"><span className="block truncate text-sm font-semibold text-slate-700">{result.title}</span><span className="mt-0.5 block truncate text-xs text-slate-400">{result.summary}</span></button>)}</div>}
        <label className="mt-3 flex items-center justify-between gap-3 text-xs font-semibold text-slate-500 md:hidden"><span>Release</span><select aria-label="Course release" value={tree?.version || ""} onChange={(event) => onVersion(event.target.value)} className="h-9 min-w-0 flex-1 rounded-lg border border-border bg-white px-2">{releases.map((release) => <option key={release.id} value={release.version}>v{release.version} · {release.status}</option>)}</select></label>
      </div>
      <nav className="min-w-0 flex-1 overflow-y-auto px-3 py-4">
        <p className="px-3 text-[10px] font-bold uppercase tracking-[.18em] text-slate-400">Course index</p>
        <div className="mt-3 space-y-1">{roots.map((item) => <div key={item.id}><PageLink item={item} active={page?.id === item.id} onNavigate={onNavigate} />{children(item.page_id).length > 0 && <div className="ml-5 border-l border-border pl-2">{children(item.page_id).map((child) => <PageLink key={child.id} item={child} active={page?.id === child.id} onNavigate={onNavigate} nested />)}</div>}</div>)}</div>
      </nav>
      <div className="border-t border-border p-4"><Link href="/" className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold text-slate-500 hover:bg-muted hover:text-slate-800"><ArrowLeft size={15} /> Back to research workspace</Link>{tree && <div className="mt-2 grid grid-cols-2 gap-2"><a href={`${API_URL}/api/v1/projects/${projectId}/course/releases/${tree.version}/export.md`} download><Button variant="outline" size="sm" className="w-full"><FileText size={13} /> Markdown</Button></a><a href={`${API_URL}/api/v1/projects/${projectId}/course/releases/${tree.version}/export.zip`} download><Button variant="outline" size="sm" className="w-full"><Download size={13} /> ZIP</Button></a></div>}</div>
    </aside>
  </>;
}

function PageLink({ item, active, onNavigate, nested = false }: { item: CoursePageSummary; active: boolean; onNavigate: (slug: string) => void; nested?: boolean }) {
  return <button onClick={() => onNavigate(item.slug)} aria-current={active ? "page" : undefined} className={cn("flex w-full min-w-0 items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition", active ? "bg-emerald-50 font-semibold text-primary" : "text-slate-600 hover:bg-muted hover:text-slate-900", nested && "text-xs")}><BookOpen size={14} className="shrink-0 opacity-60" /><span className="truncate">{item.title}</span>{item.status === "draft" && <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />}</button>;
}

function EvidenceFooter({ page }: { page: CoursePage }) {
  return <Card className="mt-10 overflow-hidden"><CardContent className="p-0"><div className="border-b border-border bg-slate-50 px-5 py-4"><div className="flex items-center gap-2"><ShieldCheck size={16} className="text-primary" /><p className="text-sm font-semibold">Evidence and provenance</p></div><p className="mt-1 text-xs text-slate-500">Atlas automatically includes claims whose quotations were independently verified. Unsupported material stays outside the course facts.</p></div>{page.claims.length > 0 && <div className="px-5 py-4"><p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Linked claims</p><ul className="mt-3 space-y-2">{page.claims.map((claim) => <li key={claim.id} className="flex items-start gap-2 text-xs leading-5 text-slate-600"><Badge value={claim.provenance} className="mt-0.5 shrink-0" /><span>{claim.text}</span></li>)}</ul></div>}{page.sources.length > 0 && <div className="border-t border-border px-5 py-4"><p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Bibliography</p><ol className="mt-3 space-y-2">{page.sources.map((source, index) => <li key={source.id} className="text-xs leading-5 text-slate-600"><a href={source.url} target="_blank" rel="noreferrer" className="hover:text-primary hover:underline">{index + 1}. {source.title || source.url}</a></li>)}</ol></div>}</CardContent></Card>;
}

function GapResearchPanel({ requests, query, setQuery, busy, onSubmit }: { requests: CourseExpansionRequest[]; query: string; setQuery: (value: string) => void; busy: boolean; onSubmit: (event: FormEvent) => void }) {
  const latest = requests[0];
  const active = latest && ["queued", "analyzing", "researching"].includes(latest.status);
  const completedTasks = latest?.tasks.filter((task) => task.status === "completed").length || 0;
  return <Card className="mb-8 overflow-hidden border-primary/20 bg-gradient-to-br from-white to-emerald-50/40"><CardContent className="p-4 sm:p-5"><div className="flex items-start gap-3"><div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary text-white shadow-sm"><Sparkles size={18} /></div><div className="min-w-0 flex-1"><p className="font-serif text-lg font-semibold text-slate-900">What is missing from this course?</p><p className="mt-1 text-xs leading-5 text-slate-500">Ask for a topic, example, tutorial, comparison, or prerequisite. Atlas checks the existing index first, creates only missing topics, and queues evidence-verified additions for your approval.</p></div></div><form onSubmit={onSubmit} className="mt-4 flex min-w-0 flex-col gap-2 sm:flex-row"><Input aria-label="Research missing course topics" value={query} onChange={(event) => setQuery(event.target.value)} disabled={busy || !!active} placeholder="For example: Add a practical tutorial on recovering interrupted LangGraph runs" className="min-w-0 flex-1" /><Button type="submit" disabled={busy || !!active || !query.trim()} className="shrink-0">{busy ? <LoaderCircle className="animate-spin" size={15} /> : <Plus size={15} />} Research missing topics</Button></form>{latest && <div className="mt-4 rounded-xl border border-border bg-white/80 px-4 py-3"><div className="flex flex-wrap items-center justify-between gap-2"><div className="min-w-0"><p className="truncate text-xs font-semibold text-slate-700">{latest.query}</p><p className="mt-1 text-[11px] leading-5 text-slate-500">{latest.result_summary || (active ? "Comparing this request with the course and knowledge graph…" : "Request recorded")}</p></div><Badge value={latest.status} /></div>{latest.discovered_topics.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{latest.discovered_topics.map((topic) => <span key={topic} className="max-w-full truncate rounded-full bg-emerald-50 px-2.5 py-1 text-[10px] font-semibold text-emerald-800">{topic}</span>)}</div>}{latest.tasks.length > 0 && <p className="mt-3 text-[10px] font-semibold uppercase tracking-widest text-slate-400">{completedTasks} of {latest.tasks.length} agent tasks complete</p>}</div>}</CardContent></Card>;
}

function DiffView({ diff, onClose }: { diff: DiffResponse | null; onClose: () => void }) {
  return <div><div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Approval workbench</p><h1 className="mt-2 font-serif text-3xl font-semibold">Release comparison</h1><p className="mt-2 text-sm text-slate-500">Review every changed page before publishing. Green lines are additions; red lines are removals.</p></div><Button variant="outline" onClick={onClose}><ArrowLeft size={14} /> Back to page</Button></div>{!diff ? <div className="grid min-h-80 place-items-center"><LoaderCircle className="animate-spin text-primary" /></div> : <div className="mt-7 space-y-5">{diff.pages.map((item) => <Card key={item.slug} className="overflow-hidden"><div className="flex flex-col gap-2 border-b border-border bg-slate-50 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"><div><p className="font-serif text-lg font-semibold">{item.title}</p><p className="mt-1 text-xs text-slate-400">{item.slug}</p></div><span className="text-xs font-semibold text-slate-500">Quality {Math.round(item.before_score)} → {Math.round(item.after_score)}</span></div><pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words p-5 font-mono text-xs leading-5 text-slate-700">{item.diff}</pre></Card>)}{!diff.pages.length && <Card><CardContent className="grid min-h-64 place-items-center text-center"><div><Check className="mx-auto text-emerald-500" /><p className="mt-3 font-semibold">No page changes survived the improvement gate.</p></div></CardContent></Card>}</div>}</div>;
}

function EmptyCourse() {
  return <div className="grid min-h-[65vh] place-items-center text-center"><div><BookOpen className="mx-auto text-slate-300" size={40} /><h1 className="mt-4 font-serif text-3xl font-semibold">Documentation is being assembled</h1><p className="mx-auto mt-3 max-w-lg text-sm leading-6 text-slate-500">Atlas turns verified research into indexed pages, evaluates bounded improvements, and then pauses for your approval. Contradictions remain visible as exceptions.</p></div></div>;
}

function headingAnchor(children: ReactNode): string {
  return String(children).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
