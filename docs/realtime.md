# Realtime

Realtime in StayOps is a **projection of committed database state**. It never carries
state the database doesn't have, and the client never treats a realtime message as data —
only as a signal that cached data is stale.

## Pathway

```
API transaction COMMIT
  → AFTER INSERT/UPDATE/DELETE trigger  stayops_private.broadcast_change()
  → realtime.send(payload, '<table>.<op>', 'org:<organization_id>', private => true)
  → Supabase Realtime: authorizes subscriber via RLS on realtime.messages
  → browser channel 'org:<organization_id>' (config.private = true)
  → RealtimeProvider: parse (Zod) → batch 120 ms → queryClient.invalidateQueries(keys)
  → TanStack Query refetches through the API (auth + tenancy re-checked)
  → UI updates; changed rows highlight for 4 s
```

Implemented in:

- `apps/api/migrations/versions/20261005_0002_integrity_security_realtime.py` (triggers, policy, functions)
- `apps/web/features/realtime/realtime-provider.tsx` (subscription, invalidation map)
- `packages/shared/src/realtime.ts` (topic + payload schema)

## Tables broadcast

| Table | Operations | Invalidates |
| --- | --- | --- |
| `reservations` | insert, update, delete | reservations (lists + that detail), command center |
| `properties` | insert, update, delete | properties, command center |
| `units` | insert, update, delete | units, properties, command center |
| `guests` | insert, update, delete | guests, reservations |
| `tasks`, `cleanings`, `maintenance_work_orders` | insert, update, delete | operations boards, unit state, command center, inbox context |
| `conversations` | insert, update, delete | inbox lists/counts, command center |
| `messages` | insert, update, delete | inbox, the open thread (`conversation_id`), command center |
| `guest_requests` | insert, update, delete | requests, inbox, command center |
| `property_knowledge` | insert, update, delete | property guides |
| `automation_workflows`, `automation_workflow_versions` | insert, update, delete | workflow library, open editor, Autopilot overview |
| `automation_executions`, `automation_step_executions` | insert, update, delete | execution history, open execution timeline, overview |
| `automation_approvals`, `operational_alerts` | insert, update, delete | approval queue, alerts, command center / bell |
| `events` | insert | event stream |
| `audit_logs` | insert | audit timelines, command center (recent activity) |

`events` status transitions (PENDING → PROCESSED) are deliberately *not* broadcast.

## Payload

Minimal, no PII:

```json
{ "table": "reservations", "operation": "UPDATE", "id": "…", "organization_id": "…",
  "status": "CHECKED_IN", "at": "2026-10-05T13:02:11Z" }
```

Audit/event notices add `action` / `event_type`, `entity_id` / `aggregate_id`;
inbox notices add `conversation_id` (migration 0005); AI notices `run_id` (0006);
Autopilot notices `workflow_id` / `execution_id` (0007). Message content, execution
context and action inputs never ride on a notice.

The guest portal does **not** use Supabase Realtime: guests have no Supabase
session and must never join an organization topic. It polls its stay-scoped
endpoints instead (see [guest-experience.md](guest-experience.md)).

## Authorization decisions

1. **Private channels only.** Every topic is `org:<uuid>` and sent with `private => true`.
   No public channel ever carries tenant data.
2. **Membership-based receive policy.** RLS policy `stayops_org_members_receive_broadcasts`
   on `realtime.messages` (`FOR SELECT TO authenticated`) allows joining a topic only if
   `stayops_private.can_receive_topic(realtime.topic())` — the topic must match
   `^org:<uuid>$` and `auth.uid()` must be a row in `organization_members` for that org.
3. **Receive-only clients.** There is no `INSERT` policy: browsers cannot publish into an
   organization's topic or spoof changes. Only the database (via `SECURITY DEFINER`
   trigger function) sends.
4. **Helper functions are locked down.** `stayops_private` is not exposed by the Data API;
   functions use `SET search_path = ''`, fully-qualified names, `REVOKE ALL FROM PUBLIC`,
   and `GRANT EXECUTE` only to `authenticated`.
5. **Writes never fail because of realtime.** The trigger wraps `realtime.send` in an
   exception handler and raises a `WARNING` instead.
6. **JWT freshness.** The client calls `supabase.realtime.setAuth(access_token)` before
   subscribing and on every auth state change (token refresh), so revoked/expired sessions
   lose access.
7. **Payloads carry no PII.** Even an authorized member only learns *that* a row changed;
   the data itself is fetched through the API.

## Failure modes

- **Channel error / timeout** → status indicator shows *Offline*; TanStack Query still
  refetches on window focus and every 60 s on the Command Center.
- **Reconnect** → on re-`SUBSCRIBED` the provider invalidates all queries once to recover
  anything missed.
- **Non-Supabase Postgres** (e.g. CI) → migration 0002 detects the absence of
  `realtime.send`/`auth.uid` and skips the realtime objects; everything else works.

## Verifying it

- Automated: `apps/web/e2e/realtime.spec.ts` — two isolated browser contexts; B watches a
  filtered list, A creates a reservation and then checks it in; B's list updates without
  a reload.
- Manual: two browsers signed in to the demo org, Command Center open in both; check a
  guest in from one.
