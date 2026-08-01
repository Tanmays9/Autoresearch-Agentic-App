# Atlas Research

Atlas Research is a local-first, agentic knowledge-graph study application. It turns a topic into parallel LangGraph research tasks, uses Azure `gpt-5.6-sol` for every agent role, validates submitted evidence, reviews the work in a fresh graph execution, and assembles indexed multi-page documentation.

Azure credentials stay in the local environment and are used only for model inference. A Brave Search API key is optional but recommended for predictable source discovery. Codex CLI remains an optional manual fallback.

## Quick start

Requirements: Docker Desktop, Node.js 20+, an Azure AI endpoint/key with a `gpt-5.6-sol` deployment, and optionally a logged-in `codex` CLI.

1. Copy `.env.example` to `.env`, set `AZURE_OPENAI_ENDPOINT`, expose `AZURE_OPENAI_API_KEY` in the host environment, and optionally add `BRAVE_SEARCH_API_KEY`.
2. Run `npm run local`.
3. Open <http://127.0.0.1:3000>.

The command creates a private local token and starts the API, crawler, LangGraph agent worker, and web containers. It also starts the host-side CLI fallback runner. The API and MCP endpoint bind only to `127.0.0.1`.

The LangGraph worker is the default. It runs up to five tasks concurrently, persists resumable state in a separate SQLite checkpoint database, and exposes only role-approved research tools. Every role—planner, researchers, reviewer, gap fixer, course architect, page writer, and documentation evaluator—uses `gpt-5.6-sol`.

The optional Codex runner starts only when work is explicitly assigned to Codex. Change local LangGraph concurrency from **Agent runs** (range 1-5).

Every research run also starts a bounded crawler. Brave Search is used when configured; otherwise manually supplied source URLs can seed the crawl. The default limit is 200 relevant public HTML, PDF, or text pages, with robots.txt, SSRF, domain, download-size, depth, and time limits.

Completed research becomes a versioned documentation course with hierarchy, full-text/semantic search, breadcrumbs, page table of contents, previous/next navigation, citations, provenance, Markdown export, and multi-file ZIP export. From any course page, **What is missing from this course?** compares a user's request with the current index and knowledge graph, creates only genuinely missing topics, and dispatches them to parallel LangGraph researchers.

Documentation autoresearch evaluates up to 12 candidates. Atlas keeps only non-regressing improvements backed by independently fetched and verified evidence, then always pauses at a LangGraph approval interrupt. Publishing is never automatic. Unsupported statements are excluded from factual pages and retained as unresolved material; evidence conflicts are resolved conservatively and remain visible in the audit trail.

## MCP clients

The MCP endpoint is available at `http://127.0.0.1:8000/mcp`. The easiest local configuration uses the stdio bridge so the local token never appears in client configuration:

```json
{
  "name": "atlas-research",
  "command": "node",
  "args": ["ABSOLUTE_PATH_TO_REPO/apps/mcp-bridge/index.mjs"]
}
```

Add that JSON through Kiro's **Add MCP Server** action or its `--add-mcp` option. Claude can use the same command through `claude mcp add atlas-research -- node ABSOLUTE_PATH_TO_REPO/apps/mcp-bridge/index.mjs`.

Once connected, say: “Create a research topic for QLoRA evaluation and start immediately.”

## Safety model

- CLI processes are spawned without a shell and never receive permission-bypass flags.
- Prompts are delivered over stdin rather than command arguments.
- Sanitized JSONL and stderr are retained up to 4 MB per execution; private reasoning, credentials, and local paths are omitted.
- Each task gets an isolated context directory.
- Agent prose is synthesis, never evidence. Cited quotations must be fetched and verified by the application.
- Verified evidence is incorporated automatically. Unsupported material is excluded from factual pages, and conflicts remain in the audit trail instead of producing repetitive manual-review cards.
- Task leases, bounded retries, depth limits, and run budgets prevent unbounded agent loops.

## Development

- API tests: `docker compose run --rm api pytest`
- Runner tests: `npm run test:runner`
- Optional logged-in Codex smoke test: set `ATLAS_LIVE_SMOKE=1`, then run `npm run smoke:codex`
- Web production build and type check: `docker compose build web`
- API reference: <http://127.0.0.1:8000/docs>
