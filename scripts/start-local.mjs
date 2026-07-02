import { mkdir, readFile, writeFile } from "node:fs/promises";
import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import path from "node:path";
import process from "node:process";

const root = path.resolve(import.meta.dirname, "..");
const localDir = path.join(root, ".local");
const tokenFile = path.join(localDir, "token");

await mkdir(localDir, { recursive: true });
await mkdir(path.join(root, "data"), { recursive: true });
try {
  const token = (await readFile(tokenFile, "utf8")).trim();
  if (token.length < 32) throw new Error("short token");
} catch {
  await writeFile(tokenFile, randomBytes(32).toString("hex"), {
    encoding: "utf8",
    mode: 0o600,
  });
}

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: root,
      stdio: "inherit",
      shell: false,
      ...options,
    });
    child.once("error", reject);
    child.once("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(`${command} exited with ${code}`)),
    );
  });
}

console.log("Starting Atlas Research services...");
await run("docker", ["compose", "up", "-d", "--build"]);
console.log("Web: http://127.0.0.1:3000");
console.log("API: http://127.0.0.1:8000/docs");
console.log("Starting host agent runner. Press Ctrl+C to stop the runner.");

const runner = spawn(process.execPath, [path.join(root, "apps/runner/index.mjs")], {
  cwd: root,
  stdio: "inherit",
  shell: false,
  env: { ...process.env, ATLAS_ROOT: root },
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => runner.kill(signal));
}

runner.once("exit", (code) => {
  process.exitCode = code ?? 0;
});

