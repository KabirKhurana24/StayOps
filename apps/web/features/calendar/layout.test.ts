import assert from "node:assert/strict";
import { test } from "node:test";

import { addDays, assignLanes, barGeometry, daysInMonth, shiftAnchor, staysOverlap, startOfWeek, visibleRange } from "./layout.ts";

test("month and week ranges", () => {
  assert.deepEqual(visibleRange("2026-10-17", "month"), { start: "2026-10-01", days: 31 });
  assert.deepEqual(visibleRange("2026-02-10", "month"), { start: "2026-02-01", days: 28 });
  assert.deepEqual(visibleRange("2026-10-07", "week"), { start: "2026-10-05", days: 7 }); // Monday
  assert.equal(startOfWeek("2026-10-11"), "2026-10-05"); // Sunday → Monday
  assert.equal(daysInMonth("2028-02-01"), 29);
});

test("navigation crosses month and year boundaries", () => {
  assert.equal(shiftAnchor("2026-12-15", "month", 1), "2027-01-01");
  assert.equal(shiftAnchor("2026-01-31", "month", -1), "2025-12-01");
  assert.equal(shiftAnchor("2026-10-05", "week", -1), "2026-09-28");
  assert.equal(addDays("2026-10-25", 1), "2026-10-26"); // DST weekend is irrelevant to calendar dates
});

test("bars span check-in afternoon to check-out morning", () => {
  // Oct 5 → Oct 8 inside a range starting Oct 1.
  assert.deepEqual(barGeometry("2026-10-05", "2026-10-08", "2026-10-01", 31), {
    start: 4.5,
    end: 7.5,
    clippedStart: false,
    clippedEnd: false,
  });
});

test("bars are clipped at range edges and dropped outside", () => {
  const g = barGeometry("2026-09-28", "2026-10-03", "2026-10-01", 31);
  assert.deepEqual(g, { start: 0, end: 2.5, clippedStart: true, clippedEnd: false });
  assert.equal(barGeometry("2026-09-20", "2026-09-25", "2026-10-01", 31), null);
  assert.equal(barGeometry("2026-11-02", "2026-11-04", "2026-10-01", 31), null);
  // Checking out on the 1st still shows the morning of day 0.
  assert.deepEqual(barGeometry("2026-09-29", "2026-10-01", "2026-10-01", 31)?.end, 0.5);
});

test("adjacent stays do not overlap; overlapping ones do", () => {
  const a = { checkIn: "2026-10-05", checkOut: "2026-10-08" };
  assert.equal(staysOverlap(a, { checkIn: "2026-10-08", checkOut: "2026-10-10" }), false);
  assert.equal(staysOverlap(a, { checkIn: "2026-10-07", checkOut: "2026-10-10" }), true);
});

test("lanes pack non-overlapping bars together", () => {
  const { lanes, placed } = assignLanes([
    { id: "a", start: 1.5, end: 4.5 },
    { id: "b", start: 4.5, end: 6.5 }, // touches a → same lane
    { id: "c", start: 2.5, end: 3.5 }, // overlaps a → second lane
  ]);
  assert.equal(lanes, 2);
  assert.deepEqual(Object.fromEntries(placed.map((p) => [p.id, p.lane])), { a: 0, c: 1, b: 0 });
});
