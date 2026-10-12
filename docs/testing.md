# Testing

## Why tiers

The backend suite used to run entirely against the remote Supabase database. Measured
from a development machine: **~312 ms per SQL round trip** and ~6 s per new
connection. A typical API test issues dozens of requests × several statements each,
so the 130-test suite took **~2.5 hours** — almost all of it network latency, not
work.

Almost none of those tests need *Supabase*; they need *PostgreSQL* (constraints,
`EXCLUDE … USING gist`, `SKIP LOCKED`, composite tenant FKs, RLS flags). So the suite
is split by what each test genuinely requires, and every tier still runs against a
real database — nothing was mocked, no test or assertion was removed.

| Tier | Marker | Needs | Runs on | Typical time |
|---|---|---|---|---|
| Unit | `unit` (automatic) | nothing | — | < 1 s |
| Database integration | `db` (automatic) | real PostgreSQL 17 | local test cluster | ~30–60 s |
| Supabase | `supabase` (explicit) | Supabase Realtime (`realtime.send`, `realtime.messages`) | the real project (`TEST_DATABASE_URL`) | ~3 min |
| Realtime / E2E | Playwright | running API + web + Supabase Realtime | real project | minutes |

Markers are applied in `tests/conftest.py`: a test that (transitively) uses the
`db_engine` fixture is `db`, otherwise `unit`; Supabase-dependent tests are marked
`@pytest.mark.supabase` by hand.

## Commands

```bash
pnpm test:api:fast      # unit + database integration on local PostgreSQL (normal dev cycle)
pnpm test:api:unit      # no database at all
pnpm test:api:supabase  # Supabase-only tests against the real project
pnpm test:api           # complete backend suite: fast tier, then Supabase tier
pnpm test:fast          # fast backend tier + frontend unit tests + typecheck/lint
pnpm test               # complete backend suite + frontend unit tests + typecheck/lint
pnpm test:e2e           # Playwright (needs `pnpm dev:api` and `pnpm dev:web` running)
```

`test:api:supabase` sets `STAYOPS_REQUIRE_SUPABASE=1`: if the database lacks a
Supabase feature the test **fails** instead of skipping, so the Supabase tier can
never pass vacuously. In the fast tier the same tests skip with an explicit reason.

## The local test cluster

`node scripts/test-db.mjs start|stop|status|url` manages a disposable cluster:

- data in `~/.stayops/test-pgdata` (outside the repo), `127.0.0.1:54329`, trust auth;
- `fsync`/`synchronous_commit`/`full_page_writes` off — durability is irrelevant here;
- database `stayops_test` with `btree_gist` and `pgcrypto` in an `extensions` schema,
  the same layout as Supabase.

`pnpm test:api:fast` starts it automatically (idempotent, < 1 s when running).

PostgreSQL 17 binaries are looked up in `$STAYOPS_PG_BIN`, then
`~/.stayops/postgres-17/pgsql/bin`, then `PATH`:

- **macOS**: `brew install postgresql@17` (and put its `bin` on `PATH`).
- **Linux**: your distribution's `postgresql-17` package (`/usr/lib/postgresql/17/bin`).
- **Windows**: unzip the official EDB binaries archive
  (`postgresql-17.x-1-windows-x64-binaries.zip` from enterprisedb.com) into
  `~/.stayops/postgres-17` — no installer or admin rights needed.

To use any other PostgreSQL 17 instead, set `STAYOPS_LOCAL_TEST_DATABASE_URL`.

## Isolation model (unchanged)

Each session migrates a fresh, randomly named schema (never `public`) with the real
Alembic migrations and drops it afterwards; each test runs inside a transaction that
is rolled back, with service commits turned into savepoints. Tests never see each
other's data and never touch real tenant data.

## Factories

`tests/factories.py` builds setup data through the **domain services** with a real
`TenantContext` (events, audit, constraints and tenant scoping all apply, subscribers
run after commit) but without HTTP. Use them when a test needs "a reservation exists";
use the API builders in `conftest.py` when the endpoint itself is under test.

## What still needs real Supabase

- `test_lifecycle.py::test_realtime_publication_path` — reservations broadcast on the
  private org topic.
- `test_operations_realtime.py::test_operations_publish_realtime_notices` — tasks,
  cleanings, work orders.
- Later phases add their own `supabase`-marked publication tests (inbox, AI runs).

The demo seed's repeatability test runs in the `db` tier: it seeds through the domain
services twice on real PostgreSQL and asserts identical results.

## AI tests

All AI tests use the deterministic `MockProvider`; no test calls a paid LLM. Tests
marked `llm_live` are skipped unless `STAYOPS_LIVE_LLM=1` and a provider key are set
(see [ai.md](ai.md)).

## Autopilot tests

`tests/test_automation_*.py`: engine units (validation, conditions, templating, DST
schedules — no database), authoring/versions/roles/tenancy, execution (idempotency,
retries, crash recovery, loop guard, turnover de-duplication), approvals (stale,
double-approve, cross-tenant), simulation (all table counts unchanged), scheduler and
AI drafting (mock provider). `test_automation_realtime.py` is in the Supabase tier.
`e2e/automations.spec.ts` covers the operator flow end to end with two browsers.

