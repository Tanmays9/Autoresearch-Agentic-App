import path from "node:path";
import process from "node:process";

import { discoverAdapters, parseAgentOutput, prepareTask, runProcess } from "./lib.mjs";

if (process.env.ATLAS_LIVE_SMOKE !== "1") {
  console.error("Set ATLAS_LIVE_SMOKE=1 to run the subscription-backed Codex smoke test.");
  process.exit(2);
}

const root = path.resolve(import.meta.dirname, "../..");
const adapter = (await discoverAdapters()).find((item) => item.provider === "codex");
if (!adapter || adapter.status !== "available") throw new Error("Codex CLI is unavailable");
const task = { id: `smoke-${Date.now()}`, role: "planning", objective: "Return a minimal research plan for LoRA evaluation." };
const context = { topic: "LoRA evaluation", project_goal: "Live adapter smoke test", codex_web_research: false };
const invocation = await prepareTask(root, task, context, "codex", adapter);
const result = await runProcess(invocation.command, invocation.args, { cwd: invocation.cwd, input: invocation.input });
if (result.code !== 0) throw new Error(result.stderr || `Codex exited with ${result.code}`);
const parsed = parseAgentOutput(result.stdout);
if (!parsed.summary) throw new Error("Codex did not return the structured research contract");
console.log(JSON.stringify({ status: "ok", provider: "codex", summary: parsed.summary }, null, 2));
