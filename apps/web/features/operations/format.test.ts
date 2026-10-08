import assert from "node:assert/strict";
import { test } from "node:test";

import { compareByPriorityThenDue, formatAge, formatDue, formatDuration, formatTurnover } from "./format.ts";

test("durations and turnover windows", () => {
  assert.equal(formatDuration(240), "4h");
  assert.equal(formatDuration(150), "2h 30m");
  assert.equal(formatDuration(45), "45m");
  assert.equal(formatTurnover(240), "4h turnover");
  assert.equal(formatTurnover(150), "2h 30m turnover");
  assert.equal(formatTurnover(null), "No next arrival");
  assert.equal(formatTurnover(-30), "Overlap");
  assert.equal(formatTurnover(4 * 24 * 60), "4d turnover");
});

test("due labels are relative and toned", () => {
  const now = Date.parse("2026-11-05T10:00:00Z");
  assert.deepEqual(formatDue("2026-11-05T08:50:00Z", now), { label: "Overdue 1h 10m", tone: "danger" });
  assert.deepEqual(formatDue("2026-11-05T11:30:00Z", now), { label: "Due in 1h 30m", tone: "warning" });
  assert.deepEqual(formatDue("2026-11-05T18:00:00Z", now), { label: "Due in 8h", tone: "muted" });
  assert.deepEqual(formatDue("2026-11-09T10:00:00Z", now), { label: "Due in 4d", tone: "muted" });
  assert.deepEqual(formatDue(null, now), { label: "No due time", tone: "muted" });
  assert.equal(formatDue("2026-11-05T08:00:00Z", now, true).label, "Done");
});

test("work order age", () => {
  const now = Date.parse("2026-11-05T10:00:00Z");
  assert.equal(formatAge("2026-11-05T09:48:00Z", now), "12m");
  assert.equal(formatAge("2026-11-05T05:00:00Z", now), "5h");
  assert.equal(formatAge("2026-11-02T10:00:00Z", now), "3d");
});

test("priority then due ordering matches the API board order", () => {
  const rows = [
    { id: "a", priority: "NORMAL" as const, due_at: "2026-11-05T09:00:00Z" },
    { id: "b", priority: "URGENT" as const, due_at: null },
    { id: "c", priority: "NORMAL" as const, due_at: "2026-11-05T08:00:00Z" },
    { id: "d", priority: "NORMAL" as const, due_at: null },
    { id: "e", priority: "HIGH" as const, due_at: "2026-11-06T08:00:00Z" },
  ];
  assert.deepEqual(
    [...rows].sort(compareByPriorityThenDue).map((r) => r.id),
    ["b", "e", "c", "a", "d"],
  );
});
