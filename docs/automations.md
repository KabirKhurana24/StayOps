# Autopilot — workflows, approvals and execution

Autopilot lets an operator define **workflows**: a trigger (an event or a schedule),
optional conditions, and a sequence of allowlisted actions. Workflows are drafted,
validated, simulated, published as immutable versions, and executed durably by the
existing outbox worker. Every automated change goes through the normal domain
service, is audited with the workflow that caused it, and is visible in realtime.

```
domain change ── event row (outbox) ──► worker: automation.triggers subscriber
                                           │ ACTIVE workflows whose *published* trigger matches
                                           │ subject reloaded from the DB (org-scoped)
                                           ▼
                            automation_executions  PENDING  (unique per workflow + event)
                                           │ drain hook: claim (SKIP LOCKED + lease)
                                           ▼
             per node: condition → evaluate + record │ action → plan + execute + record
                       approval needed → WAITING_APPROVAL (stop)       (one commit per step)
                                           ▼
                             COMPLETED | FAILED (retryable) | CANCELLED
```

## Model (migration `0007`)

| Table | Purpose |
| --- | --- |
| `automation_workflows` | Name, status (`DRAFT`/`ACTIVE`/`PAUSED`/`ARCHIVED`), **draft** definition, `current_version`, `published_trigger_type`, optimistic `revision`. |
| `automation_workflow_versions` | Immutable published snapshots (a trigger rejects `UPDATE`). Executions pin `workflow_version_id`. |
| `automation_executions` | One run: pinned version, trigger, subject, typed context, status, timings, retries, lease. Unique `(workflow_id, dedupe_key)`. |
| `automation_step_executions` | One row per node per run (unique `(execution_id, step_id)`): input/output summaries, errors, attempts. |
| `automation_approvals` | Paused actions awaiting a decision. At most one `PENDING` per step (partial unique index). |
| `operational_alerts` | Alerts raised by workflows; unique `(organization_id, dedupe_key)`; shown as Command Center signals. |

Editing the draft never changes what runs. **Publish** validates the draft, checks the
publisher holds every permission its actions need, and freezes it into version *n+1*
(no new version when nothing executable changed — node positions are ignored).

## Definition

A small typed graph, never code (`app/automation/definition.py`):

* exactly one trigger; every other node has one parent (a tree — no merges, so no
  cycles); actions continue on `next`, conditions on `true` / `false`;
* conditions: `all` / `any` groups of rules over **catalog fields**, nested at most
  two levels, ≤ 10 rules; operators `eq neq gt gte lt lte in not_in exists not_exists`,
  checked against each field's type;
* text inputs may use `{{field.path}}` placeholders — plain substitution of catalog
  fields available to the trigger;
* limits: 25 nodes, 8 conditions, 12 actions.

The catalog (`GET /api/v1/automations/catalog`) is the single allowlist shared by the
validator, simulator, editor and AI drafting.

### Triggers

Event triggers (real persisted events): `reservation.created|updated|cancelled|checked_in|checked_out`,
`task.created|assigned|completed|cancelled`, `cleaning.created|completed|approved|failed`,
`maintenance.created|assigned|resolved|closed`, `conversation.created`, `message.created`,
`guest_request.created|updated|resolved`. Optional property filter.

Scheduled triggers (evaluated by the worker every `AUTOMATION_SCHEDULE_INTERVAL_SECONDS`):

| Type | Fires | Once per |
| --- | --- | --- |
| `schedule.task_overdue` | open task past due (+N min, optional task types) | task × due time |
| `schedule.cleaning_not_ready` | turnover cleaning unfinished N min before the next check-in | task × check-in |
| `schedule.maintenance_overdue` | work order of given priorities unresolved N hours | work order × threshold |
| `schedule.task_unassigned` | open task unassigned for N minutes | task × threshold |
| `schedule.daily_summary` | per property at local HH:MM (property timezone, DST-aware) | property × local date |

The execution row *is* the persisted occurrence: `dedupe_key = schedule:<occurrence>` is
unique per workflow, so restarts or several workers can't run an occurrence twice.

### Actions (`app/automation/actions/`)

| Action | Domain service | Risk / default |
| --- | --- | --- |
| `create_task` | `tasks.create_automated_task` (source key) — `TURNOVER_CLEANING` shares `turnover:<reservation>` with the built-in checkout handler, so it is never duplicated | low · auto |
| `assign_task`, `update_task_priority` | `tasks.assign_task` / `tasks.update_task` | moderate · approval |
| `create_maintenance_work_order` | `maintenance.create_work_order` (client key), or `guest_requests.convert_to_maintenance` when triggered by a request | low; moderate + approval when it blocks the unit |
| `assign_maintenance_work_order` | `maintenance.assign_work_order` | moderate · approval |
| `create_operational_alert` | `alerts.raise_alert` (dedupe key) | low · auto |
| `create_guest_request`, `mark_guest_request_acknowledged` | `guest_requests.*` | moderate (guest-visible) · approval |
| `send_in_app_message` | `inbox.send_message` (client key) — in-app only, never claims external delivery | moderate · approval |
| `assign_conversation` | `inbox.assign_conversation` | moderate · approval |
| `write_internal_execution_note`, `request_human_approval`, `stop_workflow` | — | system |

Approval policy is per node (`default` / `required` / `auto`) except approval gates.
Refunds, charges, cancellations and other financial or irreversible actions are not
registered. Each action has `plan` (read-only validation → exact parameters, target,
summary) and `execute` (idempotent, deterministic key `wf-<execution>-<step>`).

## Execution guarantees (`app/automation/runner.py`)

* **Idempotent triggers** — unique `(workflow_id, dedupe_key)`; re-delivered events
  and re-scanned schedules never start a second run.
* **Durable steps** — each step commits together with its domain change; a crash rolls
  the unfinished step back; an expired lease lets another worker resume after the last
  completed step (never repeated). Domain calls carry deterministic keys as a second
  line of defence.
* **Errors** — domain refusals fail the step (`on_error: stop | continue`); unexpected
  errors retry automatically (`AUTOMATION_MAX_AUTO_ATTEMPTS`, backoff) then fail with a
  generic message (details only in logs).
* **Retry** — `POST /automation-executions/{id}/retry` resumes a FAILED run on its
  pinned version from the failed step; completed steps stay completed
  (`AUTOMATION_MAX_MANUAL_RETRIES`).
* **No loops** — events caused by Autopilot (`source = "automation"`) never trigger
  workflows.
* **Least privilege** — workflows act as `Autopilot · <name>` with OPERATOR authority;
  every audit entry they cause carries `metadata.automation` (workflow, version,
  execution, step — and the approver).
* No LLM or external call is made inside execution transactions.

## Approvals (`app/automation/approvals.py`)

Same contract as Phase 5 AI proposals: only an approval executes, **as the approving
human** through the domain service; versioned, `PENDING`-only, so a double click can't
execute twice. In addition the approver needs `automations:approve` *and* the action's
permissions; the action is re-planned against current data, and if the target changed
materially (status, assignee, priority) since the request it is refused as
`stale_approval` — nothing runs; retrying the execution asks again. Approve resumes the
run (worker), reject cancels it. Archiving a workflow withdraws its pending approvals.

## Simulation

`POST /automations/{id}/simulate` with an unsaved definition and a real `event_id` or
record id. Same validator, subject builder, condition evaluator and `plan` functions;
`execute` is never called and the transaction is rolled back. Output: trigger match,
condition results, which actions would run / wait for approval / fail / be skipped.
A test asserts every table's row count is unchanged.

## AI-assisted drafting

`POST /automations/draft-with-ai` (needs `automations:manage`). The model may only call
`propose_workflow`, whose schema enumerates catalog triggers, fields, operators and
actions; the result is converted and run through the same validator and returned
**unsaved** with a deterministic explanation. The operator creates the draft, edits,
simulates and publishes; no code path publishes from AI. With `AI_PROVIDER=disabled`
the endpoint returns 503 and manual authoring is unaffected. Tests use the mock.

## API

| Method & path | Permission |
| --- | --- |
| `GET /automations`, `/automations/{id}`, `/versions`, `/overview`, `/catalog`, `/templates`, `/samples` | `automations:read` |
| `POST /automations`, `PATCH /automations/{id}`, `POST …/publish|pause|resume|archive|simulate|validate`, `POST /automations/templates/{key}/use`, `POST /automations/draft-with-ai` | `automations:manage` |
| `GET /automation-executions[/{id}]` | `automations:read` |
| `POST /automation-executions/{id}/retry` | `automations:manage` |
| `GET /automation-approvals`, `POST /automation-approvals/{id}/approve|reject` | read / `automations:approve` |
| `GET /operational-alerts`, `POST …/{id}/acknowledge|resolve` | `tasks:read` / `tasks:manage` |

OWNER/ADMIN/OPERATOR manage and approve; VIEWER reads; STAFF has no access.

## Realtime

Broadcast triggers on all six tables; notices carry ids and statuses only (plus
`workflow_id` / `execution_id` routing ids) — never execution context, inputs or titles.

## Known limitations

* Branches don't merge (tree graphs); no loops, waits or delays inside a workflow.
* Scheduled triggers are a fixed documented set; no cron expressions.
* Paused workflows let already-queued runs finish; archiving cancels them.
* Execution context is captured at trigger time; conditions don't see later changes
  (actions revalidate against live data).
