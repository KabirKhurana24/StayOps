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

## Phase 7 — AI guest agent & operator copilot
- Agents act **only through domain services as an `AI_AGENT` actor** with a scoped
  permission set; every action audited and explainable; human-in-the-loop approvals.
- Guest agent answers from property knowledge and reservation context; copilot proposes
  actions on Command Center signals ("reassign cleaner", "message late guest").

## Phase 8 — Autopilot & integrations
- Rule/automation engine on the event stream (triggers, conditions, actions).
- Hostaway (and other PMS/OTA) adapter: reservation sync in, availability/rates out,
  provider ids as idempotency keys.
