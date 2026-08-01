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
  MessageSquarePlus,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Target,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { API_URL, api } from "@/lib/api";
import type { CourseExpansionRequest, CourseFeedback, CoursePage, CoursePageSummary, CourseRelease, DocumentationRun, ProjectObjective } from "@/lib/types";
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
  const [objective, setObjective] = useState<ProjectObjective | null>(null);
  const [objectiveDraft, setObjectiveDraft] = useState("");
  const [objectiveBusy, setObjectiveBusy] = useState(false);
  const [completionBusy, setCompletionBusy] = useState(false);
  const [feedbackItems, setFeedbackItems] = useState<CourseFeedback[]>([]);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackKind, setFeedbackKind] = useState<CourseFeedback["kind"]>("improve");
  const [feedbackScope, setFeedbackScope] = useState<"page" | "course">("page");
  const [feedbackMessage, setFeedbackMessage] = useState("");
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
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

  const loadFeedback = useCallback(async () => {
    try {
      const result = await api<{ items: CourseFeedback[] }>(`/api/v1/projects/${projectId}/course/feedback`);
      setFeedbackItems(result.items);
      setFeedbackError(null);
    } catch (reason) {
      setFeedbackError(reason instanceof Error ? reason.message : "Could not load feedback history");
    }
  }, [projectId]);

  const loadObjective = useCallback(async () => {
    const value = await api<ProjectObjective>(`/api/v1/projects/${projectId}/objective`);
    setObjective(value);
    setObjectiveDraft(value.objective);
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
    Promise.all([loadReleases(), loadTreeAndPage(), loadObjective()]).catch((reason) => setError(reason.message));
  }, [loadObjective, loadReleases, loadTreeAndPage]);

  useEffect(() => {
    loadFeedback().catch(() => {});
  }, [loadFeedback]);

  useEffect(() => {
    if (!gapRequests.some((item) => ["queued", "analyzing", "researching"].includes(item.status))) return;
    const timer = setInterval(() => loadReleases().catch(() => {}), 4000);
    return () => clearInterval(timer);
  }, [gapRequests, loadReleases]);

  useEffect(() => {
    if (!feedbackItems.some((item) => ["queued", "reviewing", "working"].includes(item.status))) return;
    const timer = setInterval(() => loadFeedback().catch(() => {}), 4000);
    return () => clearInterval(timer);
  }, [feedbackItems, loadFeedback]);

  useEffect(() => {
    if (!documentationRuns.some((item) => ["queued", "running"].includes(item.status))) return;
    const timer = setInterval(() => Promise.all([loadReleases(), loadObjective()]).catch(() => {}), 4000);
    return () => clearInterval(timer);
  }, [documentationRuns, loadObjective, loadReleases]);

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

  async function saveObjective() {
    const value = objectiveDraft.trim();
    if (!value) return;
    setObjectiveBusy(true);
    setError(null);
    try {
      const updated = await api<ProjectObjective>(`/api/v1/projects/${projectId}/objective`, {
        method: "PATCH",
        body: JSON.stringify({ objective: value, allow_llm_synthesis: true }),
      });
      setObjective(updated);
      setObjectiveDraft(updated.objective);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the course objective");
    } finally {
      setObjectiveBusy(false);
    }
  }

  async function runCompletionAgent() {
    if (!tree) return;
    setCompletionBusy(true);
    setError(null);
    try {
      await api(`/api/v1/projects/${projectId}/course/completion-runs`, {
        method: "POST",
        body: JSON.stringify({
          base_release_id: tree.id,
          page_budget: 24,
          allow_llm_synthesis: true,
          instructions: "Review every page and the complete course structure. Fill empty or thin pages, add missing required topics, preserve useful prior content, and clearly label model-derived synthesis.",
        }),
      });
      await Promise.all([loadObjective(), loadReleases()]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start the course completion agent");
    } finally {
      setCompletionBusy(false);
    }
  }

  function openFeedback(
    kind: CourseFeedback["kind"] = "improve",
    message = "",
    scope: "page" | "course" = page ? "page" : "course",
  ) {
    setFeedbackKind(kind);
    setFeedbackMessage(message);
    setFeedbackScope(scope);
    setFeedbackError(null);
    setFeedbackOpen(true);
  }

  async function submitFeedback(event: FormEvent) {
    event.preventDefault();
    const message = feedbackMessage.trim();
    if (!message || !tree) return;
    setFeedbackSubmitting(true);
    setFeedbackError(null);
    try {
      const request = await api<CourseFeedback>(`/api/v1/projects/${projectId}/course/feedback`, {
        method: "POST",
        body: JSON.stringify({
          kind: feedbackKind,
          message,
          release_id: tree.id,
          page_id: feedbackScope === "page" ? page?.page_id : undefined,
          allow_llm_synthesis: true,
        }),
      });
      setFeedbackItems((current) => [request, ...current.filter((item) => item.id !== request.id)]);
      setFeedbackMessage("");
    } catch (reason) {
      setFeedbackError(reason instanceof Error ? reason.message : "Could not submit feedback");
    } finally {
      setFeedbackSubmitting(false);
    }
  }

  if (loading && !tree) {
    return <div className="grid min-h-dvh place-items-center bg-background"><div className="text-center"><LoaderCircle className="mx-auto animate-spin text-primary" /><p className="mt-3 text-sm text-slate-500">Opening documentation…</p></div></div>;
  }

  return (
    <div className="min-h-dvh w-full max-w-full overflow-x-clip bg-background">
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
          <Button variant="outline" size="sm" onClick={() => openFeedback()} aria-haspopup="dialog"><MessageSquarePlus size={14} /><span className="hidden sm:inline">Feedback</span></Button>
          {tree && <a className="hidden sm:block" href={`${API_URL}/api/v1/projects/${projectId}/course/releases/${tree.version}/export.zip`} download><Button variant="outline" size="sm"><Download size={14} /><span className="hidden xl:inline">Export ZIP</span></Button></a>}
        </div>
      </header>

      {error && <div role="alert" className="mx-3 mt-3 flex items-start justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 sm:mx-5 lg:ml-[304px] lg:mr-6"><span className="flex items-start gap-2"><CircleAlert size={16} className="mt-0.5 shrink-0" />{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error"><X size={16} /></button></div>}

      {activeRun && (
        <div className="mx-3 mt-3 flex flex-col gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 sm:mx-5 sm:flex-row sm:items-center lg:ml-[304px] lg:mr-6">
          <div className="flex min-w-0 flex-1 items-start gap-3"><ShieldCheck className="mt-0.5 shrink-0 text-amber-700" size={18} /><div><p className="text-sm font-semibold text-amber-950">Documentation improvement is ready for your approval</p><p className="mt-1 text-xs leading-5 text-amber-800">{activeRun.experiments.filter((item) => item.status === "kept").length} non-regressing experiments passed the five-point improvement gate. Publishing never happens automatically.</p></div></div>
          <div className="flex shrink-0 flex-wrap gap-2"><Button variant="outline" size="sm" onClick={openDiff}><FileDiff size={14} /> Review changes</Button><Button variant="outline" size="sm" onClick={() => decide("reject")} disabled={decisionBusy}>Discard</Button><Button size="sm" onClick={() => decide("approve")} disabled={decisionBusy}>{decisionBusy ? <LoaderCircle className="animate-spin" size={14} /> : <Check size={14} />} Publish</Button></div>
        </div>
      )}

      <div className="grid min-w-0 items-start lg:grid-cols-[280px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,1fr)_240px]">
        <CourseSidebar open={navOpen} onClose={() => setNavOpen(false)} projectId={projectId} tree={tree} page={page} query={query} setQuery={setQuery} searching={searching} results={searchResults} onSearch={searchCourse} onNavigate={navigate} onVersion={selectVersion} releases={releases} />

        <main className="min-w-0 px-4 py-6 sm:px-7 lg:col-start-2 lg:px-10 lg:py-10 xl:col-start-2 2xl:px-14">
          <ObjectivePanel objective={objective} draft={objectiveDraft} setDraft={setObjectiveDraft} saving={objectiveBusy} running={completionBusy} runs={documentationRuns} onSave={saveObjective} onRun={runCompletionAgent} />
          <GapResearchPanel requests={gapRequests} query={gapQuery} setQuery={setGapQuery} busy={gapSubmitting} onSubmit={submitGap} />
          {showDiff ? (
            <DiffView diff={diff} onClose={() => setShowDiff(false)} />
          ) : page ? (
            <>
              <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1 overflow-hidden text-xs font-medium text-slate-400"><Link href="/" className="shrink-0 hover:text-primary"><Home size={13} /></Link><ChevronRight size={13} className="shrink-0" /><button onClick={() => navigate(tree?.pages[0]?.slug || "overview")} className="shrink-0 hover:text-primary">Course</button>{page.slug.split("/").map((part, index, parts) => <span key={`${part}-${index}`} className="flex min-w-0 items-center gap-1"><ChevronRight size={13} className="shrink-0" /><span className={cn("truncate capitalize", index === parts.length - 1 && "text-slate-700")}>{part.replaceAll("-", " ")}</span></span>)}</nav>
              <div className="mt-6 flex flex-wrap items-center gap-2"><Badge value={page.status} /><Badge value={page.content_provenance || "source_supported"} /><span className="text-xs font-semibold uppercase tracking-widest text-slate-400">{page.page_type.replaceAll("_", " ")}</span><span className="text-xs text-slate-300">·</span><span className="text-xs text-slate-400">Quality {Math.round(page.quality_score)}/100</span></div>
              {isPlaceholderPage(page) && <ContentGapCallout page={page} onRequest={() => openFeedback("improve", `Fill the empty or incomplete page “${page.title}” with a detailed explanation, examples, prerequisites, and practical guidance.`, "page")} />}
              <article className="prose-atlas mt-2 max-w-4xl overflow-hidden"><ReactMarkdown components={{ a: ({ ...props }) => <a {...props} rel="noreferrer" />, h2: ({ children }) => <h2 id={headingAnchor(children)}>{children}</h2>, h3: ({ children }) => <h3 id={headingAnchor(children)}>{children}</h3>, h4: ({ children }) => <h4 id={headingAnchor(children)}>{children}</h4> }}>{page.markdown}</ReactMarkdown></article>

              {(page.claims.length > 0 || page.sources.length > 0 || page.content_provenance === "llm_synthesis") && <EvidenceFooter page={page} />}

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

        <aside className="sticky top-16 col-start-3 hidden h-[calc(100dvh-4rem)] self-start overflow-y-auto border-l border-border bg-white/40 px-5 py-10 xl:block [scrollbar-gutter:stable]">
          <div><p className="text-[11px] font-bold uppercase tracking-[.16em] text-slate-400">On this page</p><nav className="mt-4 space-y-1" aria-label="Page headings">{page?.headings.map((heading) => <a key={`${heading.anchor}-${heading.level}`} href={`#${heading.anchor}`} className={cn("block border-l-2 border-transparent py-1.5 text-xs leading-5 text-slate-500 hover:border-primary hover:text-primary", heading.level > 2 && "pl-3")}>{heading.title}</a>)}{!page?.headings.length && <p className="text-xs leading-5 text-slate-400">No subheadings on this page.</p>}</nav></div>
        </aside>
      </div>
      <FeedbackDrawer
        open={feedbackOpen}
        onClose={() => setFeedbackOpen(false)}
        page={page}
        scope={feedbackScope}
        setScope={setFeedbackScope}
        kind={feedbackKind}
        setKind={setFeedbackKind}
        message={feedbackMessage}
        setMessage={setFeedbackMessage}
        submitting={feedbackSubmitting}
        error={feedbackError}
        items={feedbackItems}
        onSubmit={submitFeedback}
      />
    </div>
  );
}

function CourseSidebar({ open, onClose, projectId, tree, page, query, setQuery, searching, results, onSearch, onNavigate, onVersion, releases }: { open: boolean; onClose: () => void; projectId: string; tree: TreeResponse | null; page: CoursePage | null; query: string; setQuery: (value: string) => void; searching: boolean; results: CoursePageSummary[]; onSearch: (event: FormEvent) => void; onNavigate: (slug: string) => void; onVersion: (version: string) => void; releases: CourseRelease[] }) {
  const roots = tree?.pages.filter((item) => !item.parent_page_id) || [];
  const children = (parentId: string) => tree?.pages.filter((item) => item.parent_page_id === parentId) || [];
  return <>
    {open && <button className="fixed inset-0 z-40 bg-slate-950/35 lg:hidden" onClick={onClose} aria-label="Close navigation overlay" />}
    <aside className={cn("fixed left-0 top-0 z-50 flex h-dvh w-[min(88vw,320px)] min-w-0 max-w-full flex-col overflow-hidden border-r border-border bg-white shadow-xl transition-transform lg:fixed lg:bottom-0 lg:top-16 lg:z-20 lg:h-auto lg:w-[280px] lg:translate-x-0 lg:shadow-none", open ? "translate-x-0" : "-translate-x-full")} aria-label="Course navigation">
      <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-4 lg:hidden"><p className="font-serif font-semibold">Course index</p><Button variant="outline" size="icon" onClick={onClose} aria-label="Close course navigation"><X size={16} /></Button></div>
      <div className="min-w-0 shrink-0 border-b border-border p-4">
        <form onSubmit={onSearch} className="relative"><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={15} /><Input aria-label="Search this course" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search this course" className="h-10 pl-9 pr-11" /><Button type="submit" variant="ghost" size="icon" aria-label="Search course" disabled={searching || !query.trim()} className="absolute right-1 top-1 h-8 w-8">{searching ? <LoaderCircle className="animate-spin" size={15} /> : <ArrowRight size={15} />}</Button></form>
        {!!results.length && <div className="mt-2 max-h-52 overflow-y-auto rounded-xl border border-border bg-white p-1 shadow-soft">{results.map((result) => <button key={result.id} onClick={() => onNavigate(result.slug)} className="w-full rounded-lg px-3 py-2 text-left hover:bg-muted"><span className="block truncate text-sm font-semibold text-slate-700">{result.title}</span><span className="mt-0.5 block truncate text-xs text-slate-400">{result.summary}</span></button>)}</div>}
        <label className="mt-3 flex items-center justify-between gap-3 text-xs font-semibold text-slate-500 md:hidden"><span>Release</span><select aria-label="Course release" value={tree?.version || ""} onChange={(event) => onVersion(event.target.value)} className="h-9 min-w-0 flex-1 rounded-lg border border-border bg-white px-2">{releases.map((release) => <option key={release.id} value={release.version}>v{release.version} · {release.status}</option>)}</select></label>
      </div>
      <nav className="course-sidebar-scroll min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain px-3 py-4 [scrollbar-gutter:stable]">
        <p className="px-3 text-[10px] font-bold uppercase tracking-[.18em] text-slate-400">Course index</p>
        <div className="mt-3 space-y-1">{roots.map((item) => <div key={item.id}><PageLink item={item} active={page?.id === item.id} onNavigate={onNavigate} />{children(item.page_id).length > 0 && <div className="ml-5 border-l border-border pl-2">{children(item.page_id).map((child) => <PageLink key={child.id} item={child} active={page?.id === child.id} onNavigate={onNavigate} nested />)}</div>}</div>)}</div>
      </nav>
      <div className="min-w-0 shrink-0 border-t border-border bg-white p-4"><Link href="/" className="flex min-w-0 items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold text-slate-500 hover:bg-muted hover:text-slate-800"><ArrowLeft size={15} className="shrink-0" /><span className="truncate">Back to research workspace</span></Link>{tree && <div className="mt-2 grid min-w-0 grid-cols-2 gap-2"><a className="min-w-0" href={`${API_URL}/api/v1/projects/${projectId}/course/releases/${tree.version}/export.md`} download><Button variant="outline" size="sm" className="w-full min-w-0"><FileText size={13} /> Markdown</Button></a><a className="min-w-0" href={`${API_URL}/api/v1/projects/${projectId}/course/releases/${tree.version}/export.zip`} download><Button variant="outline" size="sm" className="w-full min-w-0"><Download size={13} /> ZIP</Button></a></div>}</div>
    </aside>
  </>;
}

function PageLink({ item, active, onNavigate, nested = false }: { item: CoursePageSummary; active: boolean; onNavigate: (slug: string) => void; nested?: boolean }) {
  return <button onClick={() => onNavigate(item.slug)} aria-current={active ? "page" : undefined} className={cn("flex w-full min-w-0 items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition", active ? "bg-emerald-50 font-semibold text-primary" : "text-slate-600 hover:bg-muted hover:text-slate-900", nested && "text-xs")}><BookOpen size={14} className="shrink-0 opacity-60" /><span className="truncate">{item.title}</span>{item.status === "draft" && <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />}</button>;
}

function EvidenceFooter({ page }: { page: CoursePage }) {
  return <Card className="mt-10 overflow-hidden"><CardContent className="p-0"><div className="border-b border-border bg-slate-50 px-5 py-4"><div className="flex items-center gap-2"><ShieldCheck size={16} className="text-primary" /><p className="text-sm font-semibold">Evidence and provenance</p></div><p className="mt-1 text-xs text-slate-500">Verified claims link to their sources. Clearly labelled LLM synthesis may also be included when the project permits it, but it is not presented as independently verified evidence.</p></div>{page.content_provenance === "llm_synthesis" && <div className="border-b border-amber-100 bg-amber-50 px-5 py-4 text-xs leading-5 text-amber-900"><span className="font-semibold">Flexible synthesis page:</span> some explanations use model knowledge because verified source coverage was incomplete. Use the linked bibliography for sourced material and review precision-sensitive details.</div>}{page.claims.length > 0 && <div className="px-5 py-4"><p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Linked claims</p><ul className="mt-3 space-y-2">{page.claims.map((claim) => <li key={claim.id} className="flex items-start gap-2 text-xs leading-5 text-slate-600"><Badge value={claim.provenance} className="mt-0.5 shrink-0" /><span>{claim.text}</span></li>)}</ul></div>}{page.sources.length > 0 && <div className="border-t border-border px-5 py-4"><p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Bibliography</p><ol className="mt-3 space-y-2">{page.sources.map((source, index) => <li key={source.id} className="text-xs leading-5 text-slate-600"><a href={source.url} target="_blank" rel="noreferrer" className="hover:text-primary hover:underline">{index + 1}. {source.title || source.url}</a></li>)}</ol></div>}</CardContent></Card>;
}

function ObjectivePanel({ objective, draft, setDraft, saving, running, runs, onSave, onRun }: { objective: ProjectObjective | null; draft: string; setDraft: (value: string) => void; saving: boolean; running: boolean; runs: DocumentationRun[]; onSave: () => void; onRun: () => void }) {
  const active = runs.find((item) => item.run_type === "completion" && ["queued", "running"].includes(item.status));
  const awaiting = runs.find((item) => item.run_type === "completion" && item.status === "awaiting_approval");
  const missing = objective?.coverage.filter((item) => ["missing", "partial"].includes(item.status)) || [];
  const dirty = !!objective && draft.trim() !== objective.objective.trim();
  return (
    <Card className="mb-6 overflow-hidden border-slate-200 bg-white">
      <CardContent className="p-4 sm:p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start">
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-900 text-white"><Target size={18} /></div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2"><p className="font-serif text-lg font-semibold text-slate-900">Project objective</p>{objective && <Badge value={active ? "reviewing" : awaiting ? "awaiting approval" : objective.status} />}</div>
              <p className="mt-1 text-xs leading-5 text-slate-500">This durable objective guides every research request, page review, and course-completion iteration.</p>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" disabled={!dirty || saving} onClick={onSave}>{saving ? <LoaderCircle className="animate-spin" size={14} /> : <Check size={14} />} Save objective</Button>
            <Button type="button" size="sm" disabled={running || !!active} onClick={onRun}>{running || active ? <LoaderCircle className="animate-spin" size={14} /> : <Sparkles size={14} />} Review and complete course</Button>
          </div>
        </div>
        <Textarea aria-label="Project course objective" value={draft} onChange={(event) => setDraft(event.target.value)} className="mt-4 min-h-24" placeholder="Describe what a complete course should enable the learner to understand or do." />
        {objective && (
          <div className="mt-4 grid gap-3 sm:grid-cols-[160px_minmax(0,1fr)]">
            <div className="rounded-xl bg-slate-50 px-4 py-3"><p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Coverage score</p><p className="mt-1 font-serif text-2xl font-semibold text-slate-900">{Math.round(objective.completion_score)}%</p><p className="mt-1 text-[11px] text-slate-500">Iteration {objective.iteration}</p></div>
            <div className="min-w-0 rounded-xl bg-slate-50 px-4 py-3"><p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Coverage still to improve</p>{missing.length ? <div className="mt-2 flex flex-wrap gap-1.5">{missing.slice(0, 8).map((item) => <span key={`${item.slug}-${item.title}`} className="max-w-full rounded-full bg-amber-50 px-2.5 py-1 text-[10px] font-semibold text-amber-800">{item.title}</span>)}</div> : <p className="mt-2 text-xs leading-5 text-slate-500">Run the course agent to create the first complete coverage map.</p>}</div>
          </div>
        )}
        <p className="mt-3 text-[11px] leading-5 text-slate-500"><span className="font-semibold text-slate-700">Flexible synthesis is enabled:</span> the agent may fill missing explanations from model knowledge, but those pages are visibly labelled and still require release approval.</p>
      </CardContent>
    </Card>
  );
}

function GapResearchPanel({ requests, query, setQuery, busy, onSubmit }: { requests: CourseExpansionRequest[]; query: string; setQuery: (value: string) => void; busy: boolean; onSubmit: (event: FormEvent) => void }) {
  const latest = requests[0];
  const active = latest && ["queued", "analyzing", "researching"].includes(latest.status);
  return (
    <Card className="mb-8 overflow-hidden border-primary/20 bg-gradient-to-br from-white to-emerald-50/40">
      <CardContent className="p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary text-white shadow-sm"><Sparkles size={18} /></div>
          <div className="min-w-0 flex-1">
            <p className="font-serif text-lg font-semibold text-slate-900">What is missing from this course?</p>
            <p className="mt-1 text-xs leading-5 text-slate-500">Ask for a topic, example, tutorial, comparison, or prerequisite. Every request is retained below, and its tasks remain visible while Atlas creates a new reviewable release.</p>
          </div>
        </div>
        <form onSubmit={onSubmit} className="mt-4 flex min-w-0 flex-col gap-2 sm:flex-row">
          <Input aria-label="Research missing course topics" value={query} onChange={(event) => setQuery(event.target.value)} disabled={busy || !!active} placeholder="For example: Add a practical tutorial on recovering interrupted LangGraph runs" className="min-w-0 flex-1" />
          <Button type="submit" disabled={busy || !!active || !query.trim()} className="shrink-0">{busy ? <LoaderCircle className="animate-spin" size={15} /> : <Plus size={15} />} Research missing topics</Button>
        </form>
        {latest && <GapRequestSummary request={latest} />}
        {requests.length > 1 && (
          <details className="mt-3 rounded-xl border border-border bg-white/60">
            <summary className="cursor-pointer list-none px-4 py-3 text-xs font-semibold text-slate-600 hover:text-primary">View all {requests.length} research requests and their tasks</summary>
            <div className="max-h-80 space-y-2 overflow-y-auto border-t border-border p-3 [scrollbar-gutter:stable]">
              {requests.slice(1).map((request) => <GapRequestSummary key={request.id} request={request} compact />)}
            </div>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

function GapRequestSummary({ request, compact = false }: { request: CourseExpansionRequest; compact?: boolean }) {
  const active = ["queued", "analyzing", "researching"].includes(request.status);
  const completedTasks = request.tasks.filter((task) => task.status === "completed").length;
  return (
    <div className={cn("rounded-xl border border-border bg-white/80 px-4 py-3", !compact && "mt-4")}>
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="break-words text-xs font-semibold leading-5 text-slate-700">{request.query}</p>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">{request.result_summary || (active ? "Comparing this request with the course and knowledge graph…" : "Request recorded")}</p>
        </div>
        <Badge value={request.status} />
      </div>
      {!compact && request.discovered_topics.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{request.discovered_topics.map((topic) => <span key={topic} className="max-w-full truncate rounded-full bg-emerald-50 px-2.5 py-1 text-[10px] font-semibold text-emerald-800">{topic}</span>)}</div>}
      {request.tasks.length > 0 && <p className="mt-3 text-[10px] font-semibold uppercase tracking-widest text-slate-400">{completedTasks} of {request.tasks.length} agent tasks complete · history retained</p>}
    </div>
  );
}

function ContentGapCallout({ page, onRequest }: { page: CoursePage; onRequest: () => void }) {
  return (
    <div className="mt-6 flex flex-col gap-4 rounded-2xl border border-amber-200 bg-amber-50 px-5 py-4 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-amber-950">This page still needs a complete draft</p>
        <p className="mt-1 text-xs leading-5 text-amber-800">Ask the course agent to fill “{page.title}”. It may use clearly labelled LLM synthesis when verified sources do not yet cover the topic.</p>
      </div>
      <Button type="button" size="sm" onClick={onRequest} className="shrink-0"><MessageSquarePlus size={14} /> Fill this page</Button>
    </div>
  );
}

function FeedbackDrawer({
  open,
  onClose,
  page,
  scope,
  setScope,
  kind,
  setKind,
  message,
  setMessage,
  submitting,
  error,
  items,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  page: CoursePage | null;
  scope: "page" | "course";
  setScope: (value: "page" | "course") => void;
  kind: CourseFeedback["kind"];
  setKind: (value: CourseFeedback["kind"]) => void;
  message: string;
  setMessage: (value: string) => void;
  submitting: boolean;
  error: string | null;
  items: CourseFeedback[];
  onSubmit: (event: FormEvent) => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, open]);

  if (!open) return null;
  const kinds: Array<{ value: CourseFeedback["kind"]; label: string; help: string }> = [
    { value: "add", label: "Add", help: "Add a missing topic, example, or page" },
    { value: "improve", label: "Improve", help: "Make existing material clearer or more complete" },
    { value: "remove", label: "Remove", help: "Remove incorrect, redundant, or unwanted material" },
    { value: "restructure", label: "Structure", help: "Reorder chapters, pages, or learning flow" },
  ];

  return (
    <>
      <button className="fixed inset-0 z-50 bg-slate-950/35" onClick={onClose} aria-label="Close feedback panel" />
      <aside role="dialog" aria-modal="true" aria-labelledby="course-feedback-title" className="fixed inset-y-0 right-0 z-[60] flex h-dvh w-[min(100vw,460px)] min-w-0 flex-col overflow-hidden border-l border-border bg-white shadow-2xl">
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-border px-5 py-5">
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[.18em] text-primary">Course agent</p>
            <h2 id="course-feedback-title" className="mt-1 font-serif text-2xl font-semibold text-slate-900">Give feedback</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">Your request is saved to project history and reviewed against the complete course objective.</p>
          </div>
          <Button type="button" variant="outline" size="icon" onClick={onClose} aria-label="Close feedback panel"><X size={16} /></Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]">
          <form onSubmit={onSubmit} className="space-y-5 border-b border-border p-5">
            <fieldset>
              <legend className="text-xs font-bold uppercase tracking-widest text-slate-400">Apply feedback to</legend>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <button type="button" disabled={!page} onClick={() => setScope("page")} className={cn("min-w-0 rounded-xl border px-3 py-3 text-left text-xs transition", scope === "page" ? "border-primary bg-emerald-50 text-primary" : "border-border text-slate-600 hover:bg-muted", !page && "cursor-not-allowed opacity-50")}><span className="block font-semibold">Current page</span><span className="mt-1 block truncate text-[11px] opacity-70">{page?.title || "No page selected"}</span></button>
                <button type="button" onClick={() => setScope("course")} className={cn("rounded-xl border px-3 py-3 text-left text-xs transition", scope === "course" ? "border-primary bg-emerald-50 text-primary" : "border-border text-slate-600 hover:bg-muted")}><span className="block font-semibold">Whole course</span><span className="mt-1 block text-[11px] opacity-70">Objective, coverage, and structure</span></button>
              </div>
            </fieldset>

            <fieldset>
              <legend className="text-xs font-bold uppercase tracking-widest text-slate-400">What should change?</legend>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {kinds.map((item) => <button key={item.value} type="button" onClick={() => setKind(item.value)} title={item.help} className={cn("rounded-xl border px-3 py-2.5 text-left text-xs font-semibold transition", kind === item.value ? "border-primary bg-primary text-white" : "border-border text-slate-600 hover:bg-muted")}>{item.label}</button>)}
              </div>
              <p className="mt-2 text-[11px] leading-5 text-slate-500">{kinds.find((item) => item.value === kind)?.help}</p>
            </fieldset>

            <label className="block">
              <span className="text-xs font-bold uppercase tracking-widest text-slate-400">Instructions for the agent</span>
              <Textarea autoFocus value={message} onChange={(event) => setMessage(event.target.value)} maxLength={4000} placeholder={scope === "page" ? "Explain what should be added, removed, or rewritten on this page…" : "Describe missing topics or how the course structure should change…"} className="mt-2" />
              <span className="mt-1 block text-right text-[10px] text-slate-400">{message.length}/4000</span>
            </label>

            <div className="rounded-xl bg-slate-50 px-4 py-3 text-[11px] leading-5 text-slate-600"><span className="font-semibold text-slate-800">Flexible evidence mode:</span> citations are preferred, but the agent may fill gaps with clearly labelled LLM synthesis. The result still becomes a reviewable release rather than changing published pages silently.</div>
            {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">{error}</p>}
            <Button type="submit" disabled={submitting || message.trim().length < 4} className="w-full">{submitting ? <LoaderCircle className="animate-spin" size={15} /> : <Sparkles size={15} />} Send to course agent</Button>
          </form>

          <section className="p-5" aria-labelledby="feedback-history-title">
            <div className="flex items-center justify-between gap-3"><h3 id="feedback-history-title" className="font-serif text-lg font-semibold">Feedback history</h3><span className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">{items.length} saved</span></div>
            <div className="mt-3 space-y-3">
              {items.map((item) => (
                <div key={item.id} className="rounded-xl border border-border p-4">
                  <div className="flex flex-wrap items-center gap-2"><Badge value={item.status} /><span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">{item.kind === "restructure" ? "structure" : item.kind}</span><span className="ml-auto text-[10px] text-slate-400">{formatFeedbackDate(item.created_at)}</span></div>
                  <p className="mt-2 break-words text-xs font-medium leading-5 text-slate-700">{item.message}</p>
                  <p className="mt-1 text-[11px] text-slate-400">{item.page_title || (item.page_id ? "Course page" : "Whole course")}</p>
                  {(item.result_summary || item.error) && <p className={cn("mt-2 rounded-lg px-3 py-2 text-[11px] leading-5", item.error ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-800")}>{item.error || item.result_summary}</p>}
                </div>
              ))}
              {!items.length && <div className="rounded-xl border border-dashed border-border px-4 py-8 text-center"><MessageSquarePlus className="mx-auto text-slate-300" size={24} /><p className="mt-2 text-xs text-slate-500">No feedback yet. Your requests will remain here instead of replacing earlier work.</p></div>}
            </div>
          </section>
        </div>
      </aside>
    </>
  );
}

function isPlaceholderPage(page: CoursePage): boolean {
  const content = page.markdown.replace(/[#*_`>\-[\]]/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
  return content.length < 180 || content.includes("no verified content is available for this section yet");
}

function formatFeedbackDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
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
