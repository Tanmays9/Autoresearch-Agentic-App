import { readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import readline from "node:readline";

const root = process.env.ATLAS_ROOT || path.resolve(import.meta.dirname, "../..");
const apiUrl = process.env.ATLAS_API_URL || "http://127.0.0.1:8000";
const token = (await readFile(path.join(root, ".local", "token"), "utf8")).trim();
const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity, terminal: false });

for await (const line of input) {
  if (!line.trim()) continue;
  try {
    const response = await fetch(`${apiUrl}/mcp`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json, text/event-stream",
        "x-local-token": token,
      },
      body: line,
    });
    if (response.status === 202) continue;
    const payload = await response.text();
    if (payload) process.stdout.write(`${payload}\n`);
  } catch (error) {
    let id = null;
    try { id = JSON.parse(line).id ?? null; } catch {}
    process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id, error: { code: -32000, message: error.message } })}\n`);
  }
}

