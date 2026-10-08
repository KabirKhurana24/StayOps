#!/usr/bin/env node
// One-shot local setup: checks prerequisites, creates .env, installs JS + Python deps.
//   pnpm bootstrap        (or: node scripts/setup.mjs [--with-browsers])
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const isWin = process.platform === "win32";
const withBrowsers = process.argv.includes("--with-browsers");

function run(cmd, args, cwd = root) {
  console.log(`\n$ ${cmd} ${args.join(" ")}`);
  // pnpm is a .cmd shim on Windows and needs a shell; args here are static literals.
  const r = isWin
    ? spawnSync([cmd, ...args].join(" "), { cwd, stdio: "inherit", shell: true })
    : spawnSync(cmd, args, { cwd, stdio: "inherit" });
  if (r.status !== 0) {
    console.error(`\n✗ "${cmd} ${args.join(" ")}" failed`);
    process.exit(r.status ?? 1);
  }
}

function has(cmd) {
  const r = isWin
    ? spawnSync(`${cmd} --version`, { stdio: "ignore", shell: true })
    : spawnSync(cmd, ["--version"], { stdio: "ignore" });
  return r.status === 0;
}

const [major] = process.versions.node.split(".").map(Number);
if (major < 22) {
  console.error(`Node ${process.versions.node} found; StayOps needs Node 22+.`);
  process.exit(1);
}
for (const [cmd, hint] of [
  ["pnpm", "corepack enable  (or npm i -g pnpm)"],
  ["uv", "https://docs.astral.sh/uv/getting-started/installation/"],
]) {
  if (!has(cmd)) {
    console.error(`Missing prerequisite: ${cmd}. Install: ${hint}`);
    process.exit(1);
  }
}

const envPath = resolve(root, ".env");
if (!existsSync(envPath)) {
  copyFileSync(resolve(root, ".env.example"), envPath);
  console.log("Created .env from .env.example — fill in your Supabase project values.");
} else {
  console.log(".env already exists — leaving it untouched.");
}

run("pnpm", ["install"]);
run("uv", ["sync"], resolve(root, "apps/api"));
if (withBrowsers) run("pnpm", ["--filter", "@stayops/web", "exec", "playwright", "install", "chromium"]);

console.log(`
✓ Setup complete. Next:
  1. Fill .env (SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_SECRET_KEY, DATABASE_URL, SEED_DEMO_PASSWORD)
  2. pnpm db:migrate
  3. pnpm db:seed
  4. pnpm dev:api     (terminal 1)  → http://localhost:8000/docs
  5. pnpm dev:web     (terminal 2)  → http://localhost:3000
`);
