#!/usr/bin/env node
// Local PostgreSQL for the fast backend test tier (database integration tests).
//
// Usage: node scripts/test-db.mjs <start|stop|status|url>
//
// The cluster is disposable and test-only: it lives outside the repo
// (~/.stayops/test-pgdata), listens on 127.0.0.1 only, uses trust auth for local
// connections and trades durability for speed (fsync off). It mirrors the parts of
// Supabase the schema relies on: PostgreSQL 17 and btree_gist/pgcrypto installed in
// an `extensions` schema. Supabase-only features (Realtime, auth schema) are NOT
// emulated — tests that need them are marked `supabase` and run against the real
// project (`pnpm test:api:supabase`).
//
// PostgreSQL binaries are found in this order:
//   1. $STAYOPS_PG_BIN                          (directory containing pg_ctl)
//   2. ~/.stayops/postgres-17/pgsql/bin         (EDB binaries zip, see docs/testing.md)
//   3. pg_ctl on PATH                           (Homebrew, apt, Postgres.app, …)
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const PORT = Number(process.env.STAYOPS_TEST_PG_PORT ?? 54329);
const DB = "stayops_test";
const HOME = join(homedir(), ".stayops");
const DATA = process.env.STAYOPS_TEST_PGDATA ?? join(HOME, "test-pgdata");
const exe = (name) => (process.platform === "win32" ? `${name}.exe` : name);

function findBin() {
  const candidates = [process.env.STAYOPS_PG_BIN, join(HOME, "postgres-17", "pgsql", "bin")].filter(Boolean);
  for (const dir of candidates) if (existsSync(join(dir, exe("pg_ctl")))) return dir;
  const which = spawnSync(process.platform === "win32" ? "where" : "which", ["pg_ctl"], { encoding: "utf8" });
  if (which.status === 0) return join(which.stdout.split(/\r?\n/)[0], "..");
  console.error(
    "PostgreSQL binaries not found. Install PostgreSQL 17 (or set STAYOPS_PG_BIN). See docs/testing.md.",
  );
  process.exit(1);
}

function run(bin, name, args, { quiet = false, allowFail = false } = {}) {
  const r = spawnSync(join(bin, exe(name)), args, { encoding: "utf8", stdio: quiet ? "pipe" : "inherit" });
  if (r.status !== 0 && !allowFail) {
    if (quiet) process.stderr.write(r.stderr ?? "");
    console.error(`${name} failed (exit ${r.status}).`);
    process.exit(r.status ?? 1);
  }
  return r;
}

const psql = (bin, db, sql, opts) =>
  run(bin, "psql", ["-h", "127.0.0.1", "-p", String(PORT), "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1", "-qtAc", sql], {
    quiet: true,
    ...opts,
  });

const isRunning = (bin) => run(bin, "pg_ctl", ["status", "-D", DATA], { quiet: true, allowFail: true }).status === 0;

export const LOCAL_TEST_DATABASE_URL = `postgresql+psycopg://postgres@127.0.0.1:${PORT}/${DB}`;

function start() {
  const bin = findBin();
  if (!existsSync(join(DATA, "PG_VERSION"))) {
    mkdirSync(DATA, { recursive: true });
    run(bin, "initdb", ["-D", DATA, "-U", "postgres", "--auth=trust", "--encoding=UTF8", "--no-locale"], { quiet: true });
    // Test-only tuning: durability is irrelevant for a disposable cluster.
    writeFileSync(
      join(DATA, "postgresql.auto.conf"),
      [
        `port = ${PORT}`,
        "listen_addresses = '127.0.0.1'",
        "fsync = off",
        "synchronous_commit = off",
        "full_page_writes = off",
        "max_connections = 100",
        "timezone = 'UTC'",
        "",
      ].join("\n"),
    );
  }
  if (!isRunning(bin)) {
    // stdio must not be piped: the server inherits pg_ctl's handles and would keep
    // the pipe open forever (spawnSync never returns on Windows).
    const r = spawnSync(join(bin, exe("pg_ctl")), ["start", "-D", DATA, "-l", join(DATA, "server.log"), "-w", "-t", "60"], {
      stdio: "ignore",
    });
    if (r.status !== 0) {
      console.error(`pg_ctl start failed (exit ${r.status}); see ${join(DATA, "server.log")}`);
      process.exit(r.status ?? 1);
    }
  }
  const exists = psql(bin, "postgres", `SELECT 1 FROM pg_database WHERE datname = '${DB}'`).stdout.trim();
  if (exists !== "1") psql(bin, "postgres", `CREATE DATABASE ${DB}`);
  // Same layout as Supabase: extensions live in their own schema.
  psql(
    bin,
    DB,
    "CREATE SCHEMA IF NOT EXISTS extensions; " +
      "CREATE EXTENSION IF NOT EXISTS btree_gist SCHEMA extensions; " +
      "CREATE EXTENSION IF NOT EXISTS pgcrypto SCHEMA extensions;",
  );
  console.log(`local test PostgreSQL ready on 127.0.0.1:${PORT} (${DATA})`);
}

function stop() {
  const bin = findBin();
  if (isRunning(bin)) run(bin, "pg_ctl", ["stop", "-D", DATA, "-m", "fast", "-w"], { quiet: true });
  console.log("local test PostgreSQL stopped");
}

const command = process.argv[2];
if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("test-db.mjs")) {
  if (command === "start") start();
  else if (command === "stop") stop();
  else if (command === "status") console.log(isRunning(findBin()) ? "running" : "stopped");
  else if (command === "url") console.log(LOCAL_TEST_DATABASE_URL);
  else {
    console.error("Usage: node scripts/test-db.mjs <start|stop|status|url>");
    process.exit(2);
  }
}
