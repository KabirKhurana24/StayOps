import assert from "node:assert/strict";
import { test } from "node:test";

import { addDays, daysBetween, todayIn, utcToZoned, zonedToUtcIso } from "./time.ts";

test("property-local wall clock converts to UTC", () => {
  assert.equal(zonedToUtcIso("2026-10-05", "15:00", "Europe/Amsterdam"), "2026-10-05T13:00:00.000Z"); // CEST +2
  assert.equal(zonedToUtcIso("2026-10-05", "15:00", "Europe/London"), "2026-10-05T14:00:00.000Z"); // BST +1
  assert.equal(zonedToUtcIso("2026-10-05", "15:00", "Asia/Kolkata"), "2026-10-05T09:30:00.000Z"); // IST +5:30
  assert.equal(zonedToUtcIso("2026-10-05", "15:00", "America/New_York"), "2026-10-05T19:00:00.000Z"); // EDT -4
});

test("DST transitions keep the local check-in time", () => {
  // Europe/Paris leaves summer time on 2026-10-25: same 15:00 local, one hour later in UTC.
  assert.equal(zonedToUtcIso("2026-10-24", "15:00", "Europe/Paris"), "2026-10-24T13:00:00.000Z");
  assert.equal(zonedToUtcIso("2026-10-26", "15:00", "Europe/Paris"), "2026-10-26T14:00:00.000Z");
  // Spring forward (2026-03-29): 11:00 local after the jump is +2.
  assert.equal(zonedToUtcIso("2026-03-29", "11:00", "Europe/Paris"), "2026-03-29T09:00:00.000Z");
  // Round trip across the boundary.
  assert.deepEqual(utcToZoned("2026-10-26T14:00:00.000Z", "Europe/Paris"), { date: "2026-10-26", time: "15:00" });
});

test("midnight boundaries are evaluated in the property's zone, not UTC", () => {
  // 23:30 UTC on Oct 5 is already Oct 6 in Amsterdam and still Oct 5 in New York.
  const instant = new Date("2026-10-05T23:30:00Z");
  assert.equal(todayIn("Europe/Amsterdam", instant), "2026-10-06");
  assert.equal(todayIn("America/New_York", instant), "2026-10-05");
  assert.deepEqual(utcToZoned("2026-10-05T22:00:00Z", "Europe/Amsterdam"), { date: "2026-10-06", time: "00:00" });
});

test("calendar date arithmetic", () => {
  assert.equal(addDays("2026-10-31", 1), "2026-11-01");
  assert.equal(daysBetween("2026-10-24", "2026-10-27"), 3); // spans DST end, still 3 nights
});
