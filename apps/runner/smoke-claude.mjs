import { runProcess } from "./lib.mjs";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const schema = {
  type: "object",
  properties: { summary: { type: "string" } },
  required: ["summary"],
  additionalProperties: false,
};

const result = await runProcess(
  "claude",
  [
    "-p",
    "Return one JSON object with summary set to local runner smoke test.",
    "--output-format",
    "json",
    "--json-schema",
    JSON.stringify(schema),
    "--permission-mode",
    "dontAsk",
    "--tools",
    "Read,WebSearch,WebFetch",
    "--no-session-persistence",
  ],
  { timeoutMs: 120_000 },
);

const diagnostic = JSON.stringify(result, null, 2);
await mkdir(path.resolve(import.meta.dirname, "../../.local"), { recursive: true });
await writeFile(path.resolve(import.meta.dirname, "../../.local/claude-smoke.json"), diagnostic, "utf8");
console.log(diagnostic);
// Always exit successfully so diagnostic stdout is preserved by wrappers.
process.exitCode = 0;
