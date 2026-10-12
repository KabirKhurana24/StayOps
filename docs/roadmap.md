# Roadmap

Each phase plugs into the Phase 1 foundation: tenant context, permissions, domain
services, the event log/outbox, the audit log and realtime invalidation.

## Phase 1 — Foundation ✅ (this release)
Multi-tenant auth & roles, properties/units, guests, reservations (list, timeline,
create/edit, state machine, no-double-booking), Command Center with operational signals,
idempotent domain events + dispatcher, audit timeline, private realtime, deterministic
seed, backend + E2E tests.

## Operations: tasks, cleaning, maintenance ✅
- `tasks` (turnover cleaning, inspection, maintenance, guest request) with assignees,
  priorities, due times; `cleanings` (persisted checklist, inspection) and maintenance
  work orders that block unit availability through the existing availability service.
- Subscribers: `reservation.checked_out` → turnover cleaning (tight-turnover aware, in the
  property's timezone); `reservation.*` → re-plan deadlines; `task.completed` → inspection;
  `maintenance.*` → `unit.blocked` / `unit.unblocked`.
- STAFF act only on work assigned to them. PostgreSQL outbox worker with leases, retries
  with backoff and parking (see [worker.md](worker.md)) — no broker needed.
- Later: member invitations and role management; mobile-first cleaner view.

## Guest experience: unified inbox + guest portal ✅
- Conversations (one per stay and channel), ordered messages with per-side read
  state, internal notes and operational system notes; realtime inbox.
- Guest requests that convert into tasks or work orders and resolve themselves when
  the work is done; structured property knowledge with PUBLIC / STAY / INTERNAL
  visibility; tokenized per-stay guest portal (stay, guide, messages, requests).
- Channel adapter interface; in-app connected, Twilio / WhatsApp / Hostaway / Airbnb /
  Booking.com report NOT_CONFIGURED. See [guest-experience.md](guest-experience.md).
- Later: provider integrations, ID/registration and upsells in the portal.

## Phase 5 — Payments, expenses, owner portal
- Stripe adapter (payment intents, payouts, webhooks → idempotent events).
- Expenses per property/unit; owner statements; owner role + portal with scoped access.

## Phase 6 — Analytics
- Occupancy, ADR, RevPAR, lead time, channel mix; event-sourced projections.

## AI guest assistant & operator Copilot ✅
- Agents act **only through typed, permission-checked tools that call domain services**,
  as an `AI_AGENT` actor; every step persisted, evented and audited; consequential
  actions are proposals a human approves. Provider abstraction (OpenAI, deterministic
  mock); guardrails; evaluation suite. See [ai.md](ai.md).
- Later: proactive Copilot suggestions on signals, assistant replies on external
  channels once providers are connected, vector retrieval for long guides.

## Autopilot: workflows, approvals, AI-assisted drafting ✅
- Versioned workflows on the event stream and a small set of scheduled triggers;
  typed conditions with branching; allowlisted actions through domain services;
  durable, idempotent execution in the existing worker; human approval with
  revalidation; simulation; visual builder; templates; realtime. See
  [automations.md](automations.md).
- Later: waits/delays inside workflows, merging branches, more actions as modules
  (payments, integrations) land.

## Phase 8 — Integrations
- Hostaway (and other PMS/OTA) adapter: reservation sync in, availability/rates out,
  provider ids as idempotency keys.
