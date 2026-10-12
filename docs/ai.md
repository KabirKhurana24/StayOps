# AI: guest assistant and operator Copilot (Phase 5)

Two agents, one architecture. Neither has database access: they act only through
explicit, typed, permission-checked tools that call domain services.

```
User ─► start run (API) ─► ContextBuilder ─► LLMProvider ─► tool calls
                                                   │
             ┌─────────────────────────────────────┘
             ▼
   validate args (Pydantic) ─► check permission (server context) ─► domain service
             │                                                       │
             │   mutation tools ─► proposal (PENDING) ─► human approves ─► domain service (as the human)
             ▼                                                       ▼
   ai_run_steps row committed ─► realtime notice ─► live UI        events + audit
```

## Configuration

| Setting | Values | Default |
|---|---|---|
| `AI_PROVIDER` | `disabled`, `mock`, `openai` | `disabled` — nothing answers guests until you opt in |
| `OPENAI_API_KEY` | secret | — (never logged, stored or echoed in errors) |
| `OPENAI_MODEL` | model id | `gpt-4.1-mini` |
| `AI_TIMEOUT_SECONDS` / `AI_MAX_TOOL_ROUNDS` | | 30 / 4 |
| `AI_INPUT_COST_PER_MTOK` / `AI_OUTPUT_COST_PER_MTOK` | USD | for cost estimates |

`GET /api/v1/ai/status` reports whether a provider is configured; the UI hides AI
features otherwise.

## Providers (`app/ai/providers`)

* `LLMProvider.complete(LLMRequest) -> LLMResponse` — chat messages + tool schemas
  in, text or tool calls + token usage out. Errors: `LLMTimeoutError`,
  `LLMProviderError`.
* `OpenAIProvider` — Chat Completions with function calling over plain `httpx`
  (no SDK dependency). Invalid tool-argument JSON is surfaced, never guessed.
* `MockProvider` — deterministic: a keyword policy picks tools, and every answer is
  composed **only from tool results**. Used by all tests, the evaluation suite and
  E2E. `MockProvider(script=[...])` replays responses/exceptions for failure tests.

## Runs, steps, proposals (`app/ai/models.py`, migration 0006)

* `ai_runs` — agent type, requester (Copilot) or guest/stay/conversation (assistant),
  the triggering message *reference*, status (`RUNNING`, `COMPLETED`,
  `AWAITING_APPROVAL`, `ESCALATED`, `FAILED`), answer, evidence, provider/model,
  input/output tokens, estimated cost, error code. A guest message triggers at most
  one run (unique `trigger_message_id`).
* `ai_run_steps` — every step as it happens (context, model turn, tool, proposal,
  guardrail, answer) with status and duration. Committed immediately; database
  triggers publish them, so the UI's "Checking your reservation…" states are real.
* `ai_action_proposals` — what the Copilot wants to change, with reason, affected
  entity and expected effect. Nothing executes until a human approves.

Runs execute on a background thread after the request returns (API) or inline
(tests). The agent context is rebuilt from the database at execution: the guest's
stay scope from the reservation, the operator's role from their **current**
membership. A `RUNNING` run older than 10 minutes is reported as failed.

## Guest assistant

Triggered by a guest's text message in the portal (when the provider is configured
and the thread's assistant isn't paused by an operator). Context: the guest's own
stay, today's date in the property's timezone, and the list of house-guide topics.

Tools: `get_current_reservation`, `get_property_info`, `get_property_knowledge`,
`get_guest_profile`, `get_conversation_history`, `get_check_in_information`,
`get_check_out_information`, `get_guest_request_status`, `create_guest_request`,
`request_early_checkin`, `request_late_checkout`, `escalate_to_team`.

* Knowledge comes only from `knowledge.guest_entries` (visibility rules, stay window,
  unit targeting): no INTERNAL entries; door codes and Wi-Fi only from arrival day.
* Early check-in / late checkout become requests; the assistant says the team will
  confirm. It cannot approve, grant, refund, cancel or change dates.
* Its reply is posted in the thread as `sender_type = AI` ("{Host} assistant"),
  visible to the team; the guest's message stays unread for the team when the
  assistant escalates.

## Operator Copilot

`POST /api/v1/copilot/ask` (permission `copilot:use`: owner, admin, operator,
viewer). Runs are private to the person who asked.

Read tools: `get_today_operations`, `get_open_tasks`, `get_overdue_tasks`,
`get_active_maintenance`, `get_blocked_units`, `get_tight_turnovers`,
`get_unresolved_guest_requests`, `get_unread_conversations`, `get_reservation`,
`get_guest`, `get_property`, `get_task`, `get_maintenance` — each with the operator's
own role (a viewer's Copilot can't read what a viewer can't).

Proposal tools: `assign_task`, `create_task`, `create_maintenance` — validated
against current state at proposal time; executed only by
`POST /api/v1/ai/proposals/{id}/approve`, through the normal domain service **as the
approving user**. `ai_action.approved` (with outcome `executed` / `failed` and the
reason) or `ai_action.rejected` is audited. Proposals are versioned: approving twice
executes once.

## Guardrails (`app/ai/guardrails.py`) — deterministic, every provider

* **Before the model** (guest): refunds/payments, cancellations, date changes →
  fixed reply + escalation; other guests' data and internal/system details → fixed
  refusal. No provider call is made.
* **After the model**: any 4+ digit value not present in this run's tool results or
  context is a hallucination (codes, prices) → fallback; the guest assistant may never
  claim to have approved/booked/cancelled/refunded/changed anything; empty answers
  fall back.
* **Failures** — provider timeout or error, invalid tool arguments, unknown tool,
  missing permission (step `BLOCKED`), tool exception (rolled back in a savepoint),
  runaway tool loops — all end in a truthful deterministic message. Success text can
  only come from successful tool results.

## Events and audit

Through the existing event system (`record_change`, actor type `AI_AGENT`, actor id =
run id): `agent.started`, `agent.tool_called`, `agent.tool_completed`,
`agent.completed`, `agent.escalated`, `agent.failed`, `ai_action.proposed`,
`ai_action.approved`, `ai_action.rejected`. Audit metadata: agent, requesting user,
tool, input summary (ids/enums; free text summarised by length), result summary,
approval state. Never API keys; never raw guest text (runs reference the message).

## Realtime

`ai_runs`, `ai_run_steps`, `ai_action_proposals` broadcast on `org:<id>` with
`run_id` (migration 0006). The Copilot screen and the inbox thread follow runs live;
the guest portal polls its stay-scoped `/guest/assistant` endpoint.

## Testing

* `pytest -m eval` — 14 cases (8 guest, 6 operator): tool usage, grounding,
  tenant/guest isolation, permission boundaries, no hallucinated state, escalation.
* AI unit/integration tests run on `MockProvider`; `AI_PROVIDER` is forced to
  `disabled` for the test process and providers are injected explicitly.
* `llm_live` tests (real OpenAI) are skipped unless `STAYOPS_LIVE_LLM=1` and
  `OPENAI_API_KEY` are set.
* E2E: `playwright.config.ts` starts the API with `AI_PROVIDER=mock`.
