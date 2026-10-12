# Event worker & operations automation

StayOps has one event system: the `events` table is both the domain event log and
the transactional outbox (see [architecture.md](architecture.md)). The worker is
the outbox's read side — plain PostgreSQL, no broker.

## Lifecycle of an event

```
API transaction ── state change + event row (PENDING) + audit entry ──► commit
                                                                          │
                     wake (same process) or poll                          ▼
Worker ── claim batch (UPDATE … FOR UPDATE SKIP LOCKED, lease) ──► PROCESSING
            │
            ├─ every subscriber ok (each in a savepoint) ──► COMPLETED
            └─ a subscriber raised ──► rollback of handler work ──► FAILED
                                         next_attempt_at = 2s, 4s, 8s … (≤ 30 min)
                                         after EVENT_MAX_ATTEMPTS: parked (next_attempt_at NULL)
```

* **Claiming** is atomic and uses `SKIP LOCKED`, so any number of workers can run.
* **Lease**: a `PROCESSING` row whose `locked_at` is older than `EVENT_LEASE_SECONDS`
  belonged to a crashed worker and is claimed again; the late worker's result is
  discarded (it no longer holds the lease).
* **Idempotency**: handlers may run more than once. Everything they create is keyed
  deterministically — `tasks (organization_id, source_key)` is unique
  (`turnover:<reservation_id>`, `inspection:<cleaning_id>:<attempt>`), and events
  they emit use idempotency keys derived from the source event id.
* **Tenant context**: handlers act as `TenantContext.system(event.organization_id)`
  ("Automation") and scope every query by that organization.
* **Synchronous validation stays synchronous**: e.g. completing a cleaning with an
  unfinished checklist is rejected by the API request, never failed in the worker.
* **Observability**: structured logs `event.processed` (status, attempt, worker,
  next attempt) and `worker.*`; failed rows keep `last_error`.

## Running it

| Mode | How |
|---|---|
| Development | The API runs the worker on a background thread (`EmbeddedWorker`) and wakes it after each commit. Toggle with `EVENT_WORKER_EMBEDDED`. |
| Production | `node scripts/api.mjs worker --loop 2` (or `uv run python -m app.events.worker --loop 2`), any number of replicas. Set `EVENT_WORKER_EMBEDDED=false` on API nodes. |
| Scripts, seed, tests | Inline: events are processed in the caller's thread right after commit. |
| Re-arm parked events | `uv run python -m app.events.worker --requeue-failed` (after fixing the cause). |

## Operations subscribers (`app/operations/handlers.py`)

| Event | Handler |
|---|---|
| `reservation.checked_out` | Create the `TURNOVER_CLEANING` task + cleaning (checklist), due by the next arrival in the unit (or the property's default check-in time), `HIGH` priority when the turnover is tight (`TIGHT_TURNOVER_MINUTES`, default 180). Computed in the property's timezone. |
| `reservation.created/updated/cancelled` | Re-plan open turnover cleanings in the affected unit(s); escalate (never silently downgrade) priority when a turnover becomes tight. |
| `task.completed` (turnover) | Cleaning → `INSPECTION_REQUIRED` and an `INSPECTION` task, or `APPROVED` when `CLEANING_REQUIRES_INSPECTION=false`. |
| `maintenance.*` | Announce `unit.blocked` / `unit.unblocked` when a unit's maintenance block changes. |

Unit availability never waits for the worker: an active work order with
`blocks_unit` *is* the block, and the reservations availability service reads it
synchronously. `unit.blocked` / `unit.unblocked` are a record of what happened.

## Autopilot (drain hooks)

After each drain the worker runs registered *drain hooks*
(`app.events.dispatcher.register_drain_hook`). Autopilot registers one that evaluates
scheduled triggers (worker loop only, rate-limited) and runs PENDING workflow
executions (`SKIP LOCKED` claim + lease, one commit per step); events those steps emit
are drained in the next round. Inline dispatch (scripts, tests) runs the hook too, so
tests see the same cascade as production. See [automations.md](automations.md).

