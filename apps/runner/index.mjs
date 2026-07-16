import path from "node:path";
import process from "node:process";

import {
  discoverAdapters,
  loadToken,
  parseAgentOutput,
  prepareTask,
  runProcess,
  runnerIdentity,
} from "./lib.mjs";

const root = process.env.ATLAS_ROOT || path.resolve(import.meta.dirname, "../..");
const apiUrl = process.env.ATLAS_API_URL || "http://127.0.0.1:8000";
const token = await loadToken(root);
const runnerId = runnerIdentity();
const adapters = await discoverAdapters();
const enabledProviders = new Set(
  (process.env.ATLAS_ENABLED_PROVIDERS || "codex")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean),
);
for (const adapter of adapters) {
  if (!enabledProviders.has(adapter.provider)) {
    adapter.status = "unsupported";
    adapter.diagnostic = `Disabled by local runner configuration (enabled: ${[...enabledProviders].join(", ")}).`;
  }
}
const adapterMap = new Map(adapters.map((item) => [item.provider, item]));
const headlessProviders = adapters.filter((item) => item.mode === "headless" && item.status === "available");
const active = new Map();
let researchSettings = { codex_concurrency: 3, codex_web_research: true };
let stopping = false;

function registrationPayload() {
  return {
    runner_id: runnerId,
    hostname: runnerId.split("-").slice(1, -1).join("-"),
    providers: adapters.map(({ help, ...item }) => item),
  };
}

async function api(pathname, init = {}) {
  const response = await fetch(`${apiUrl}${pathname}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      "x-local-token": token,
      ...(init.headers || {}),
    },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(body?.detail || `${response.status} ${response.statusText}`);
  return body;
}

async function waitForApi() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`${apiUrl}/health`);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  throw new Error("Atlas API did not become ready");
}

await waitForApi();
await api("/api/v1/runner/register", {
  method: "POST",
  body: JSON.stringify(registrationPayload()),
});

console.log(`Atlas host runner ${runnerId}`);
for (const adapter of adapters) {
  console.log(`  ${adapter.provider}: ${adapter.status}${adapter.version ? ` (${adapter.version})` : ""}`);
}

function createExecutionLogger(executionId) {
  let queued = [];
  let flushing = false;
  const flush = async () => {
    if (flushing || !queued.length) return;
    flushing = true;
    const batch = queued.splice(0, 100);
    try {
      await api(`/api/v1/runner/executions/${executionId}/events`, {
        method: "POST",
        body: JSON.stringify({ runner_id: runnerId, events: batch }),
      });
    } catch (error) {
      queued = [...batch, ...queued].slice(0, 500);
      console.error(`Could not persist execution log: ${error.message}`);
    } finally {
      flushing = false;
    }
  };
  const timer = setInterval(() => flush().catch(() => {}), 500);
  return {
    push(event) {
      queued.push(event);
      if (queued.length >= 20) flush().catch(() => {});
    },
    async close() {
      clearInterval(timer);
      while (flushing) await new Promise((resolve) => setTimeout(resolve, 25));
      for (let attempt = 0; queued.length && attempt < 3; attempt += 1) {
        await flush();
        if (queued.length) await new Promise((resolve) => setTimeout(resolve, 150));
      }
    },
  };
}

async function execute(provider, taskEnvelope, controller) {
  const task = taskEnvelope.task;
  const executionId = taskEnvelope.execution_id;
  const adapter = adapterMap.get(provider);
  let heartbeat;
  const logger = createExecutionLogger(executionId);
  try {
    const invocation = await prepareTask(root, task, taskEnvelope.context, provider, adapter);
    logger.push({ stream: "system", event_type: "process_start", content: JSON.stringify({ provider, role: task.role, objective: task.objective }) });
    heartbeat = setInterval(async () => {
      try {
        const state = await api(`/api/v1/runner/tasks/${task.id}/heartbeat`, {
          method: "POST",
          body: JSON.stringify({ runner_id: runnerId, provider }),
        });
        if (state.cancel_requested) controller.abort();
      } catch {}
    }, 5_000);
    console.log(`[${provider}] ${task.role}: ${task.objective}`);
    const completed = await runProcess(invocation.command, invocation.args, {
      cwd: invocation.cwd,
      input: invocation.input,
      signal: controller.signal,
      onEvent: (event) => logger.push(event),
    });
    if (completed.code !== 0) {
      const diagnostic = [completed.stderr.trim(), completed.stdout.trim()].filter(Boolean).join("\n");
      throw Object.assign(new Error(diagnostic || `CLI exited with ${completed.code}`), { exitCode: completed.code });
    }
    const result = parseAgentOutput(completed.stdout);
    logger.push({ stream: "system", event_type: "structured_result", content: JSON.stringify(result) });
    await logger.close();
    await api(`/api/v1/runner/tasks/${task.id}/submit`, {
      method: "POST",
      body: JSON.stringify({
        runner_id: runnerId,
        provider,
        cli_version: adapter.version,
        prompt_version: task.role === "review" ? "review-v1" : "research-v2",
        result,
      }),
    });
    console.log(`[${provider}] completed ${task.id}`);
  } catch (error) {
    logger.push({ stream: "system", event_type: "execution_error", content: String(error.message ?? error).slice(0, 4000) });
    await logger.close();
    console.error(`[${provider}] ${error.message}`);
    if (/auth|login|credential|not signed in/i.test(String(error.message))) {
      adapter.status = "authentication_required";
      adapter.diagnostic = String(error.message).slice(0, 500);
      await api("/api/v1/runner/register", {
        method: "POST",
        body: JSON.stringify(registrationPayload()),
      }).catch(() => {});
    }
    await api(`/api/v1/runner/tasks/${task.id}/fail`, {
      method: "POST",
      body: JSON.stringify({
        runner_id: runnerId,
        provider,
        exit_code: error.exitCode ?? null,
        diagnostic: String(error.message ?? error).slice(0, 4000),
      }),
    }).catch((reportError) => console.error(`Could not report task failure: ${reportError.message}`));
  } finally {
    if (heartbeat) clearInterval(heartbeat);
    active.delete(executionId);
  }
}

/*
 * Claim work by execution, not by provider. This allows several isolated Codex
 * processes to run at once while every task retains its own lease and logs.
 */
async function claimAvailable(adapter) {
  const limit = adapter.provider === "codex" ? researchSettings.codex_concurrency : 1;
  const providerActive = () => [...active.values()].filter((item) => item.provider === adapter.provider).length;
  while (!stopping && adapter.status === "available" && providerActive() < limit) {
    const envelope = await api("/api/v1/runner/tasks/claim", {
      method: "POST",
      body: JSON.stringify({
        runner_id: runnerId,
        provider: adapter.provider,
        cli_version: adapter.version,
      }),
    });
    if (!envelope.task) break;
    const controller = new AbortController();
    const record = { provider: adapter.provider, taskId: envelope.task.id, controller, promise: null };
    active.set(envelope.execution_id, record);
    record.promise = execute(adapter.provider, envelope, controller);
  }
}

async function tick() {
  if (stopping) return;
  try {
    researchSettings = await api("/api/v1/settings/research");
  } catch {}
  await api("/api/v1/runner/heartbeat", {
    method: "POST",
    body: JSON.stringify({ runner_id: runnerId, busy_providers: [...new Set([...active.values()].map((item) => item.provider))] }),
  }).catch(() => {});
  for (const adapter of headlessProviders) {
    if (adapter.status !== "available") continue;
    try {
      await claimAvailable(adapter);
    } catch (error) {
      console.error(`Claim failed for ${adapter.provider}: ${error.message}`);
    }
  }
}

const timer = setInterval(() => tick().catch((error) => console.error(error.message)), 4000);
await tick();

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, async () => {
    if (stopping) return;
    stopping = true;
    clearInterval(timer);
    console.log("Waiting for active tasks to finish...");
    for (const item of active.values()) item.controller.abort();
    await Promise.allSettled([...active.values()].map((item) => item.promise));
    process.exit(0);
  });
}
