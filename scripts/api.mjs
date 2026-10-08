#!/usr/bin/env node
// Cross-platform wrapper for backend tasks (works on Windows without `make`).
// Usage: node scripts/api.mjs <dev|migrate|seed|test|test-fast|test-unit|test-supabase|check|worker> [extra args]
//
// Test tiers (docs/testing.md):
//   test-unit      no database
//   test-fast      unit + database integration on the local PostgreSQL test cluster (seconds)
//   test-supabase  tests that need Supabase itself (Realtime), against TEST_DATABASE_URL;
//                  a missing Supabase feature fails instead of skipping
//   test           test-fast, then test-supabase (the complete suite)
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const apiDir = resolve(scriptsDir, "../apps/api");
const LOCAL_TEST_DB = `postgresql+psycopg://postgres@127.0.0.1:${process.env.STAYOPS_TEST_PG_PORT ?? 54329}/stayops_test`;
const startLocalDb = ["node", resolve(scriptsDir, "test-db.mjs"), "start"];
const fastEnv = { TEST_DATABASE_URL: process.env.STAYOPS_LOCAL_TEST_DATABASE_URL ?? LOCAL_TEST_DB };
const supabaseEnv = { STAYOPS_REQUIRE_SUPABASE: "1" };
const [task, ...extra] = process.argv.slice(2);

const tasks = {
  dev: [["uv", "run", "uvicorn", "app.main:app", "--reload", "--port", "8000", ...extra]],
  migrate: [["uv", "run", "alembic", "upgrade", "head", ...extra]],
  seed: [["uv", "run", "python", "-m", "app.seed", ...extra]],
  worker: [["uv", "run", "python", "-m", "app.events.worker", ...extra]],
  "test-unit": [["uv", "run", "pytest", "-m", "unit", ...extra]],
  "test-fast": [startLocalDb, [fastEnv, "uv", "run", "pytest", "-m", "not supabase", ...extra]],
  "test-supabase": [[supabaseEnv, "uv", "run", "pytest", "-m", "supabase", ...extra]],
  test: [
    startLocalDb,
    [fastEnv, "uv", "run", "pytest", "-m", "not supabase", ...extra],
    [supabaseEnv, "uv", "run", "pytest", "-m", "supabase", ...extra],
  ],
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

for (const step of tasks[task]) {
  const env = typeof step[0] === "object" ? { ...process.env, ...step[0] } : process.env;
  const [cmd, ...args] = typeof step[0] === "object" ? step.slice(1) : step;
  // uv ships a native executable on every platform, so no shell is needed.
  const result = spawnSync(cmd, args, { cwd: apiDir, stdio: "inherit", env });
  if (result.error) {
    console.error(`Failed to run ${cmd}: ${result.error.message}. Is uv installed? https://docs.astral.sh/uv/`);
    process.exit(1);
  }
  if (result.status !== 0) process.exit(result.status ?? 1);
}
