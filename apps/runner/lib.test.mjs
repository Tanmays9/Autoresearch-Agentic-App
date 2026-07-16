import assert from "node:assert/strict";
import test from "node:test";

import { buildCodexCommand, buildPrompt, kiroMcpDefinition, parseAgentOutput, researchSchema, runProcess } from "./lib.mjs";

test("topic text is passed through stdin and never through a shell", () => {
  const malicious = "QLoRA; Remove-Item -Recurse C:\\important";
  const adapter = {
    status: "available",
    capabilities: { exec: true, json: true, read_only_sandbox: true, skip_git_check: true, output_schema: true, browser_use: true, shell_tool_toggle: true, ephemeral: true },
  };
  const prompt = buildPrompt({ role: "research", objective: malicious }, { topic: malicious }, researchSchema);
  const command = buildCodexCommand(adapter, prompt, "C:\\tmp\\schema.json");
  assert.equal(command.command, "codex");
  assert.equal(command.args.at(-1), "-");
  assert.equal(command.input, prompt);
  assert.ok(!command.args.some((value) => value.includes("dangerously")));
  assert.ok(command.args.includes("read-only"));
  assert.deepEqual(command.args.slice(command.args.indexOf("--enable"), command.args.indexOf("--enable") + 2), ["--enable", "browser_use"]);
  assert.deepEqual(command.args.slice(command.args.indexOf("--disable"), command.args.indexOf("--disable") + 2), ["--disable", "shell_tool"]);
});

test("parses Claude structured output wrapper", () => {
  const output = JSON.stringify({ structured_output: { summary: "done", claims: [] } });
  assert.deepEqual(parseAgentOutput(output), { summary: "done", claims: [] });
});

test("parses JSONL agent message", () => {
  const output = [
    JSON.stringify({ type: "thread.started" }),
    JSON.stringify({ item: { text: JSON.stringify({ summary: "researched", subtopics: [] }) } }),
  ].join("\n");
  assert.equal(parseAgentOutput(output).summary, "researched");
});

test("strict output schema requires every declared evidence field", () => {
  const evidence = researchSchema.properties.claims.items.properties.evidence.items;
  assert.deepEqual(new Set(evidence.required), new Set(Object.keys(evidence.properties)));
});

test("Kiro MCP configuration uses a fixed local bridge command", () => {
  const definition = kiroMcpDefinition("C:\\atlas");
  assert.equal(definition.name, "atlas-research");
  assert.equal(definition.command, process.execPath);
  assert.ok(definition.args[0].endsWith("apps\\mcp-bridge\\index.mjs"));
});

test("process input is delivered through stdin and output events stream", async () => {
  const events = [];
  const result = await runProcess(
    process.execPath,
    ["-e", "process.stdin.on('data', d => process.stdout.write(JSON.stringify({type:'echo',value:d.toString()})+'\\n'))"],
    { input: "safe prompt; no shell", onEvent: (event) => events.push(event), timeoutMs: 5000 },
  );
  assert.equal(result.code, 0);
  assert.match(result.stdout, /safe prompt; no shell/);
  assert.equal(events[0].event_type, "echo");
});

test("reasoning events are not forwarded to the live log", async () => {
  const events = [];
  await runProcess(
    process.execPath,
    ["-e", "console.log(JSON.stringify({type:'item.completed',item:{type:'reasoning',text:'private'}})); console.log(JSON.stringify({type:'item.completed',item:{type:'agent_message',text:'public'}}))"],
    { onEvent: (event) => events.push(event), timeoutMs: 5000 },
  );
  assert.equal(events.length, 1);
  assert.match(events[0].content, /public/);
});
