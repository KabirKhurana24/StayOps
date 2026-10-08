# StayOps

**Open-source, AI-native property operations for short-term rental operators.**

StayOps is an operating system for a rental business. Its primary screen answers one
question: *what needs attention, what changed, and what should happen next?*

> **Status: Phase 2 — reservations + live calendar.** On top of the Phase 1 foundation
> (multi-tenant auth, idempotent events, full audit log, private realtime), operators get a
> redesigned app shell, reservation lifecycle actions (confirm, check in/out, cancel),
> live availability with overlap protection, a month/week calendar with drag-to-move, and
> reservation-derived Command Center metrics. See [docs/roadmap.md](docs/roadmap.md).

---

## Architecture at a glance

```
Browser (Next.js 16, React 19, TanStack Query)
   │  REST + Bearer JWT (Supabase session in cookies)        ▲ private broadcast
   ▼                                                         │ "org:<id>" (RLS-authorized)
FastAPI modular monolith ── SQLAlchemy 2 ──► PostgreSQL ─────┘ AFTER triggers → realtime.send()
   │  every mutation, in ONE transaction:                        (Supabase Realtime)
   │    state change → domain event (outbox, idempotent) → audit entry
   └─► event dispatcher (after commit) → subscribers (logging today; automation/webhooks/AI later)
```

- **Database is the source of truth.** Realtime is a projection of committed rows: the
  broadcast only says *"reservation X changed"*; clients refetch through the authorized API.
- **Tenancy is enforced three times**: API membership/permission checks, tenant-scoped
  queries, and composite foreign keys that make cross-tenant rows impossible in Postgres.
- **Every action is explainable**: who, what, when, which fields changed, which request.

Full detail: [docs/architecture.md](docs/architecture.md) · [docs/data-model.md](docs/data-model.md) ·
[docs/realtime.md](docs/realtime.md) · [docs/security.md](docs/security.md)

## Stack

| Layer | Choice |
| --- | --- |
| Web | Next.js 16.3 (App Router, Turbopack, React Compiler), React 19.2, TypeScript 6.0, Tailwind CSS 4.3, Radix (shadcn-style components), TanStack Query 5, Zod 4 |
| API | Python 3.11+, FastAPI 0.142, Pydantic 2.13, SQLAlchemy 2.1 (sync, psycopg 3), Alembic 1.20, structlog |
| Data | PostgreSQL (Supabase), btree_gist exclusion constraints, RLS deny-by-default |
| Auth / realtime | Supabase Auth (cookie sessions via `@supabase/ssr`), Supabase Realtime private broadcast |
| Tooling | pnpm workspaces, uv, Ruff, mypy (strict), ESLint 9, Pytest 9, Playwright 1.63 |
| Infra | Docker Compose (optional), Redis (wired, not yet used) |

## Repository layout

```
stayops/
├── apps/
│   ├── api/                 FastAPI modular monolith
│   │   ├── app/
│   │   │   ├── main.py      app factory, middleware, routers
│   │   │   ├── config/      settings (env)
│   │   │   ├── core/        errors, logging, request IDs, pagination, time, idempotency
│   │   │   ├── db/          base, session, model registry
│   │   │   ├── auth/        JWT verification, tenant context, permissions
│   │   │   ├── organizations/ properties/ guests/ reservations/   domain modules
│   │   │   ├── command_center/   operational state + signals
│   │   │   ├── events/      domain events, idempotent publisher, outbox dispatcher, worker
│   │   │   ├── audit/       audit log + timeline API
│   │   │   ├── realtime/    topic conventions (triggers live in migration 0002)
│   │   │   ├── health/      liveness/readiness
│   │   │   └── seed/        deterministic demo dataset
│   │   ├── migrations/      Alembic
│   │   └── tests/           Pytest (real Postgres, disposable schema)
│   └── web/                 Next.js app
│       ├── app/             routes: (auth)/login|signup, (app)/…, onboarding, auth/callback
│       ├── components/      ui primitives, providers, shell
│       ├── features/        command-center, reservations, properties, guests, activity, realtime, …
│       ├── lib/             api client, supabase clients, formatting, env
│       ├── e2e/             Playwright specs
│       └── proxy.ts         session refresh + route gating (Next 16 "proxy")
├── packages/shared/         enums, Zod API contract, timezone + realtime helpers
├── infra/                   container build files
├── docs/                    architecture, realtime, data model, security, roadmap
└── scripts/                 cross-platform task runners (setup, api tasks)
```

## Local setup

### Prerequisites

- Node.js **22+** and **pnpm 10+** (`corepack enable`)
- **uv** ([install](https://docs.astral.sh/uv/getting-started/installation/)) — manages Python 3.11+ and the API virtualenv
- A **Supabase project** (free tier is fine) — provides Postgres, Auth and Realtime

Docker is *not* required.

### 1. Install

```bash
git clone <repo> stayops && cd stayops
pnpm bootstrap          # checks tools, creates .env from .env.example, pnpm install, uv sync
```

### 2. Configure Supabase (database setup)

Create a project at [supabase.com](https://supabase.com), then fill `.env` in the repo root
(one file is shared by the API and the web app):

| Variable | Where to find it | Used by |
| --- | --- | --- |
| `SUPABASE_URL` | Project Settings → API | API (JWT issuer/JWKS), web |
| `SUPABASE_PUBLISHABLE_KEY` | Project Settings → API Keys (publishable, or legacy `anon`) | web |
| `SUPABASE_SECRET_KEY` | API Keys (secret, or legacy `service_role`) — **server only** | seed script only |
| `SUPABASE_JWT_SECRET` | JWT Keys → legacy secret (optional) | API, only for HS256 tokens |
| `DATABASE_URL` | Connect → **Session pooler** string, prefixed `postgresql+psycopg://` | API, migrations, tests |
| `TEST_DATABASE_URL` | optional; defaults to `DATABASE_URL` | tests (disposable schema) |
| `API_CORS_ORIGINS` | `http://localhost:3000` | API |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | web |
| `SEED_DEMO_EMAIL` / `SEED_DEMO_PASSWORD` | choose a dev password | seed + Playwright |
| `REDIS_URL` | optional | reserved for Phase 2 |

For a frictionless demo, disable **Auth → Providers → Email → Confirm email** in the
dashboard (sign-up works either way; with confirmation on, users confirm via email first).

### 3. Migrate and seed

```bash
pnpm db:migrate         # alembic upgrade head (tables, constraints, RLS, realtime triggers)
pnpm db:seed            # demo org "Canal Stays" + demo login (SEED_DEMO_EMAIL / _PASSWORD)
```

The seed is deterministic and repeatable: it wipes and rebuilds the demo organization,
with reservations positioned relative to *today in each property's timezone* (yesterday,
today, the next few days), so the Command Center always has arrivals, departures, in-house
guests, a same-day turnover, a late arrival and a unit-in-maintenance conflict. It also plays
the team's day through the real services: three staff members, turnover cleanings created
by the check-out automation (approved, awaiting inspection, a tight turnover in progress,
an overdue one, a failed inspection), a cleaner-reported AC fault that blocks a unit, and
open / on-hold / resolved / closed work orders.

### 4. Run

```bash
pnpm dev:api            # http://localhost:8000  (OpenAPI docs at /docs)
pnpm dev:web            # http://localhost:3000
```

Sign in with the demo credentials. To see realtime, open a second browser (or a private
window), sign in again, and create or check in a reservation in one — the other updates
within a second, with no refresh.

## Tests and checks

```bash
pnpm test:api           # pytest — 84 tests: lifecycle, availability, tenancy, events, audit, realtime, time/DST, integrity …
pnpm test:unit          # node --test — calendar geometry + timezone helpers (no deps)
pnpm check:api          # ruff lint + format check + mypy --strict
pnpm check:web          # tsc (shared + web) + eslint --max-warnings 0
pnpm test:e2e           # Playwright: lifecycle, filters, availability conflict, calendar, mobile; two-browser realtime
pnpm test               # test:api + test:unit + check:web
```

Backend tests run real Alembic migrations into a **disposable schema** on your database
(never `public`), wrap each test in a rolled-back transaction, then drop the schema.
Playwright needs the API, web app and seed data; it starts the servers itself if they
are not running (`reuseExistingServer`).

## Realtime architecture (summary)

1. API commits a change (e.g. reservation updated).
2. An `AFTER` trigger calls `realtime.send(payload, 'reservations.update', 'org:<org_id>', private => true)`.
3. Supabase Realtime authorizes each subscriber with RLS on `realtime.messages`: only
   authenticated **members of that organization** can join `org:<org_id>`; nobody can publish.
4. The browser's `RealtimeProvider` batches notices and invalidates TanStack Query keys.
5. Queries refetch through the API (which re-checks auth); the UI updates and briefly
   highlights changed rows. A `Live` indicator shows channel health; on reconnect everything refetches.

Details and authorization decisions: [docs/realtime.md](docs/realtime.md).

## Useful commands

| Task | Command |
| --- | --- |
| New migration | `cd apps/api && uv run alembic revision -m "describe change"` |
| Drain/retry the event outbox (see [docs/worker.md](docs/worker.md)) | `node scripts/api.mjs worker --loop 5` |
| Containerised stack | `docker compose up --build` (optional) |

## Roadmap

Operations (tasks, turnover cleaning, maintenance, outbox worker) are in. Next: unified inbox, guest portal, payments,
owner portal, analytics, AI guest agent, AI operator copilot, automations, integrations
(Hostaway, Stripe, Twilio, OpenAI). See [docs/roadmap.md](docs/roadmap.md).

## License

To be decided by the maintainers before the first public release (no LICENSE file yet).
