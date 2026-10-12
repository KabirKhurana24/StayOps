import assert from "node:assert/strict";
import { test } from "node:test";

import type { AIStep } from "@stayops/shared";

import { evidenceHref, formatCost, isLive, visibleSteps } from "./model.ts";

const step = (over: Partial<AIStep>): AIStep => ({
  id: crypto.randomUUID(),
  seq: 1,
  kind: "TOOL",
  status: "COMPLETED",
  label: "Checking overdue tasks",
  tool_name: "get_overdue_tasks",
  input_summary: {},
  result_summary: {},
  duration_ms: 12,
  created_at: "2026-10-09T08:00:00Z",
  ...over,
});

test("evidence links open the right screen", () => {
  assert.equal(evidenceHref({ type: "task", id: "t1", label: "x" }), "/tasks?t=t1");
  assert.equal(evidenceHref({ type: "work_order", id: "w1", label: "x" }), "/maintenance?wo=w1");
  assert.equal(evidenceHref({ type: "conversation", id: "c1", label: "x" }), "/inbox?c=c1");
  assert.equal(evidenceHref({ type: "knowledge", id: "k1", label: "x" }), null);
});

test("raw model turns are hidden unless they failed", () => {
  const steps = [
    step({ kind: "CONTEXT" }),
    step({ kind: "LLM" }),
    step({ kind: "TOOL" }),
    step({ kind: "LLM", status: "FAILED" }),
    step({ kind: "ANSWER" }),
  ];
  assert.deepEqual(
    visibleSteps(steps).map((s) => `${s.kind}:${s.status}`),
    ["CONTEXT:COMPLETED", "TOOL:COMPLETED", "LLM:FAILED", "ANSWER:COMPLETED"],
  );
});

test("run liveness and cost formatting", () => {
  assert.ok(isLive("RUNNING"));
  assert.ok(!isLive("AWAITING_APPROVAL"));
  assert.equal(formatCost(0), "—");
  assert.equal(formatCost(0.00123), "$0.0012");
  assert.equal(formatCost(0.5), "$0.50");
});
