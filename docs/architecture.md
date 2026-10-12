# Architecture

StayOps is a **modular monolith** with a typed web client. One deployable API, one
database, clear domain boundaries, and an event log that later phases (automation,
webhooks, integrations, AI) plug into without touching the code that emits events.

## Components

| Component | Responsibility |
| --- | --- |
| `apps/web` (Next.js 16) | Operator UI. Client components fetch through the API with TanStack Query; `proxy.ts` refreshes the Supabase session cookie and gates routes. |
| `apps/api` (FastAPI) | The only writer to the database. Authenticates every request, resolves the tenant, authorizes by permission, runs domain logic in a transaction. |
| PostgreSQL (Supabase) | Source of truth. Constraints enforce invariants (tenancy, no double-booking, value ranges). RLS denies direct Data API access. |
| Supabase Auth | Identity. Issues the JWTs the API verifies (JWKS for asymmetric keys, legacy HS256 optional). |
| Supabase Realtime | Fan-out of change notices on private per-organization topics. |
| `packages/shared` | The contract: enums, Zod response schemas, timezone and realtime helpers. |

## Request lifecycle (a mutation)

```
PATCH /api/v1/reservations/{id}
 │ RequestContextMiddleware  → request_id (inbound X-Request-ID if well-formed, else new), bound to logs
 │ get_current_user          → verify JWT (alg-pinned), upsert profile mirror
 │ get_tenant_context        → membership lookup (X-Organization-Id must be a membership) → role
 │ require(RESERVATIONS_WRITE) → permission check (roles map to permissions)
 │ service.update_reservation (one transaction)
 │    ├─ tenant-scoped load (foreign rows are 404, indistinguishable from missing)
 │    ├─ state machine + optimistic concurrency (version column, expected_version)
 │    ├─ friendly availability pre-check; EXCLUDE constraint guarantees it under races
 │    ├─ flush → version bump
 │    └─ record_change → INSERT event (ON CONFLICT DO NOTHING on idempotency key) + audit row
 │ commit_and_dispatch       → COMMIT, then dispatch the new events to subscribers
 │                              (failures mark the event FAILED for retry; the write stands)
 └ AFTER triggers (in the same commit) → realtime.send(...) to org:<org_id>
```

## Domain modules

Each module owns `models.py`, `schemas.py` (DTOs — ORM models are never returned),
`service.py` (domain logic; receives a `TenantContext`) and `router.py` (HTTP only).

- `organizations` — organizations, members, roles, profiles (mirror of `auth.users`)
- `properties` — properties (with IANA timezone + default check-in/out times) and units
- `guests` — guest records (email unique per org, case-insensitive), preferences, private notes
- `tasks`, `cleaning`, `maintenance`, `operations` — operational work and automation (Phase 3)
- `inbox` — conversations, ordered messages, read state, system notes (Phase 4)
- `guest_requests` — guest requests and their conversion into tasks / work orders
- `knowledge` — structured property knowledge (the guest guide; AI grounding source)
- `guest_portal` — hashed per-stay access links and guest-facing read models
- `channels` — `ChannelAdapter` boundary (in-app connected; providers not configured)
- `ai` — providers, orchestrator, tools, guardrails, runs/steps/proposals (Phase 5, see [ai.md](ai.md))
- `automation`, `alerts` — Autopilot workflows, versions, executions, approvals, scheduled
  triggers and operational alerts (Phase 6, see [automations.md](automations.md))
- `reservations` — booking lifecycle, state machine, availability
- `command_center` — read model: today per property timezone, metrics, signals
- `events` — `DomainEvent`, idempotent `publish`, `EventDispatcher`, subscriber registry, worker
- `audit` — immutable audit entries, sanitized metadata, timeline API

## Event system

- The `events` table is both the **domain event log** and the **transactional outbox**.
  Events are written in the same transaction as the change they describe.
- `(organization_id, idempotency_key)` is unique. Keys are deterministic per change
  (`reservation.updated:<id>:v<version>`) or derived from a client `Idempotency-Key`
  header (`reservation.created:client:<key>`). A replayed command/webhook can't emit twice;
  a replayed create returns the original resource.
- Dispatch claims rows with `SELECT … FOR UPDATE SKIP LOCKED` and only processes `PENDING`/
  `FAILED` rows, so concurrent workers never double-process. Each handler runs in a savepoint.
- Phase 1 dispatches in-process right after commit and ships one subscriber (structured
  logging of event id / org / type / status). `python -m app.events.worker --loop 5`
  drains/retries the outbox; Phase 2 can move this behind Redis without changing emitters.

Future modules subscribe by pattern:

```python
@registry.subscribe("reservation.*", name="cleaning-scheduler")
def schedule_turnover(session, event: EventEnvelope) -> None: ...
```

## Actors and AI-readiness

Every service call carries an `Actor` (`USER`, `SYSTEM`, `INTEGRATION`, `AI_AGENT`) inside
the `TenantContext`. Events record `actor_id` and `source`; audit rows record actor type,
id and label. When the AI copilot arrives it will call the **same domain services** with an
`AI_AGENT` actor and a scoped permission set — never raw SQL — and every action it takes
will be audited like an operator's.

## Integrations (future)

Integrations enter through an adapter boundary: an adapter translates external payloads
into domain commands (with idempotency keys derived from the provider's ids, e.g.
`integration:hostaway:reservation:12345`) and subscribes to domain events for outbound
sync. The core never imports provider SDKs.

## Frontend structure

- `app/` routes only; screens live in `features/<domain>/`.
- `lib/api/client.ts` — attaches the bearer token (from the cookie-backed Supabase
  session) and active org, parses the error envelope into `ApiError`, validates every
  response with the shared Zod schema.
- `features/realtime/realtime-provider.tsx` — private channel subscription → batched
  query invalidation → row highlights; connection status indicator.
- Server state lives only in TanStack Query. There is no client-side store of core entities.

## Observability

- structlog: JSON outside development, console locally. A redaction processor scrubs
  values under keys matching password/secret/token/jwt/authorization/api_key/cookie.
- One `http.request` line per request: method, path (never query string), status,
  duration, request_id. Uvicorn's access log is disabled to avoid duplicates.
- Event lifecycle logs: `event.published`, `event.duplicate`, `event.processed`
  (with `status`), `event.dispatch_skipped`.
- Every error response includes `request_id`, also returned as `X-Request-ID`.

## Why these choices

- **Sync SQLAlchemy** — FastAPI runs sync endpoints in a threadpool; simpler code and
  transactions, ample throughput for this stage. Async can be introduced per-endpoint.
- **VARCHAR + CHECK enums** — strongly typed in Python/TypeScript and the DB, without
  native-enum migration pain.
- **Realtime as invalidation, not payload** — no PII on the wire, a single authorization
  path (the API), and no client-side reconciliation bugs.
