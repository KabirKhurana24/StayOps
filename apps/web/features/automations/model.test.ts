import assert from "node:assert/strict";
import { test } from "node:test";

import type { ExecutionDetail, ExecutionStep } from "@stayops/shared";

import { entityHref, formatDuration, successRate, timeline } from "./model.ts";

const step = (over: Partial<ExecutionStep>): ExecutionStep => ({
  id: crypto.randomUUID(),
  seq: 1,
  step_id: "s",
  step_type: "create_task",
  label: "Create task",
  status: "COMPLETED",
  attempt: 1,
  input_summary: {},
  output_summary: {},
  error_code: null,
  error_message: null,
  started_at: "2026-10-09T10:42:02Z",
  completed_at: "2026-10-09T10:42:03Z",
  ...over,
});

const detail = (over: Partial<ExecutionDetail>): ExecutionDetail => ({
  id: crypto.randomUUID(),
  workflow_id: crypto.randomUUID(),
  workflow_name: "Same-Day Turnover Protection",
  workflow_version: 2,
  trigger_type: "reservation.updated",
  triggering_event_id: crypto.randomUUID(),
  status: "COMPLETED",
  entity_type: "reservation",
  entity_id: crypto.randomUUID(),
  entity_label: "SO-ABC123 · Unit 202",
  started_at: "2026-10-09T10:42:01Z",
  completed_at: "2026-10-09T10:42:04Z",
  duration_ms: 3000,
  error_summary: null,
  actions_executed: 2,
  retry_count: 0,
  last_retry_at: null,
  correlation_id: null,
  trigger: { type: "reservation.updated" },
  fields: {},
  steps: [],
  approvals: [],
  can_retry: false,
  ...over,
});

test("the timeline reflects persisted steps only, in order", () => {
  const d = detail({
    steps: [
      step({ step_type: "trigger", label: "Reservation updated: SO-ABC123" }),
      step({ step_type: "condition", label: "Same-day", output_summary: { result: true } }),
      step({ label: "Cleaning", output_summary: { summary: "Created task “Turnover”" } }),
      step({ label: "Alert", status: "SKIPPED" }),
      step({ label: "Assign", status: "FAILED", error_message: "The task is completed", attempt: 2 }),
    ],
    status: "FAILED",
    error_summary: "Assign: The task is completed",
  });
  const entries = timeline(d);
  assert.deepEqual(
    entries.map((e) => e.title),
    [
      "Event received",
      "Workflow matched",
      "Condition passed · Same-day",
      "Cleaning",
      "Assign (attempt 2)",
      "Workflow failed",
    ],
  );
  assert.equal(entries[3].detail, "Created task “Turnover”");
  assert.equal(entries[4].tone, "danger");
  assert.equal(entries.at(-1)?.detail, "Assign: The task is completed");
});

test("waiting and scheduled runs are labelled truthfully", () => {
  const waiting = timeline(detail({ status: "WAITING_APPROVAL", completed_at: null, triggering_event_id: null }));
  assert.equal(waiting[0].title, "Scheduled occurrence due");
  assert.equal(waiting.at(-1)?.title, "Paused until someone decides");
});

test("links, durations and rates", () => {
  assert.equal(entityHref("task", "t1"), "/tasks?t=t1");
  assert.equal(entityHref("work_order", "w1"), "/maintenance?wo=w1");
  assert.equal(entityHref("cleaning", "c1"), "/cleaning?c=c1");
  assert.equal(entityHref("guest_request", "g1"), null);
  assert.equal(entityHref(null, "x"), null);
  assert.equal(formatDuration(450), "450 ms");
  assert.equal(formatDuration(3200), "3.2 s");
  assert.equal(formatDuration(95_000), "1 min 35 s");
  assert.equal(formatDuration(null), "—");
  assert.equal(successRate(0.857), "86%");
  assert.equal(successRate(null), "—");
});
