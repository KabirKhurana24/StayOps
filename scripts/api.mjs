#!/usr/bin/env node
// Cross-platform wrapper for backend tasks (works on Windows without `make`).
// Usage: node scripts/api.mjs <dev|migrate|seed|test|check|worker> [extra args]
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const apiDir = resolve(dirname(fileURLToPath(import.meta.url)), "../apps/api");
const [task, ...extra] = process.argv.slice(2);

const tasks = {
  dev: [["uv", "run", "uvicorn", "app.main:app", "--reload", "--port", "8000", ...extra]],
  migrate: [["uv", "run", "alembic", "upgrade", "head", ...extra]],
  seed: [["uv", "run", "python", "-m", "app.seed", ...extra]],
  worker: [["uv", "run", "python", "-m", "app.events.worker", ...extra]],
  test: [["uv", "run", "pytest", ...extra]],
  check: [
    ["uv", "run", "ruff", "check", "."],
    ["uv", "run", "ruff", "format", "--check", "."],
    ["uv", "run", "mypy", "app"],
  ],
};

if (!tasks[task]) {
  console.error(`Unknown task "${task}". Available: ${Object.keys(tasks).join(", ")}`);
  process.exit(2);
}

for (const [cmd, ...args] of tasks[task]) {
  // uv ships a native executable on every platform, so no shell is needed.
  const result = spawnSync(cmd, args, { cwd: apiDir, stdio: "inherit" });
  if (result.error) {
    console.error(`Failed to run ${cmd}: ${result.error.message}. Is uv installed? https://docs.astral.sh/uv/`);
    process.exit(1);
  }
  if (result.status !== 0) process.exit(result.status ?? 1);
}
