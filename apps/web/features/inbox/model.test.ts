import assert from "node:assert/strict";
import { test } from "node:test";

import type { Message } from "@stayops/shared";

import { groupByDay, messageSide, portalUrl, previewLine, requestActions, unreadLabel, visiblePanes } from "./model.ts";

const msg = (over: Partial<Message>): Message => ({
  id: crypto.randomUUID(),
  conversation_id: "c",
  seq: 1,
  sender_type: "GUEST",
  sender_id: null,
  sender_label: null,
  message_type: "TEXT",
  content: "hi",
  internal: false,
  metadata: {},
  created_at: "2026-10-08T09:00:00Z",
  read_at: null,
  ...over,
});

test("list preview prefixes the team's and internal messages", () => {
  const last = (over: Partial<Message>) => ({ last_message: { ...msg(over), created_at: "2026-10-08T09:00:00Z" } });
  assert.equal(previewLine({ last_message: null }), "No messages yet");
  assert.equal(previewLine(last({ content: "Is there parking?" })), "Is there parking?");
  assert.equal(previewLine(last({ sender_type: "STAFF", content: "Yes!" })), "You: Yes!");
  assert.equal(previewLine(last({ sender_type: "STAFF", internal: true, content: "VIP" })), "Note: VIP");
  assert.equal(previewLine(last({ sender_type: "AI", content: "Check-in is 15:00" })), "Assistant: Check-in is 15:00");
});

test("unread badge text", () => {
  assert.equal(unreadLabel(0), null);
  assert.equal(unreadLabel(3), "3");
  assert.equal(unreadLabel(250), "99+");
});

test("thread layout: guest left, team right, StayOps centred", () => {
  assert.equal(messageSide(msg({ sender_type: "GUEST" })), "left");
  assert.equal(messageSide(msg({ sender_type: "STAFF" })), "right");
  assert.equal(messageSide(msg({ sender_type: "AI" })), "right");
  assert.equal(messageSide(msg({ sender_type: "SYSTEM", message_type: "STATUS" })), "center");
});

test("messages group under local-day headings in the property's timezone", () => {
  const now = new Date("2026-10-08T10:00:00Z");
  const days = groupByDay(
    [
      msg({ seq: 1, created_at: "2026-10-07T08:00:00Z" }),
      msg({ seq: 2, created_at: "2026-10-07T22:30:00Z" }), // 00:30 on the 8th in Amsterdam
      msg({ seq: 3, created_at: "2026-10-08T09:00:00Z" }),
    ],
    now,
    "Europe/Amsterdam",
  );
  assert.deepEqual(
    days.map((d) => [d.label, d.messages.map((m) => m.seq)]),
    [
      ["Yesterday", [1]],
      ["Today", [2, 3]],
    ],
  );
});

test("request actions follow the lifecycle and existing links", () => {
  const open = requestActions({ status: "OPEN", task: null, work_order: null });
  assert.deepEqual(open, { acknowledge: true, toTask: true, toMaintenance: true, resolve: true, close: false, cancel: true });
  const linked = requestActions({ status: "IN_PROGRESS", task: { id: "t", title: "Towels", status: "TODO" }, work_order: null });
  assert.equal(linked.toTask, false);
  assert.equal(linked.acknowledge, false);
  const resolved = requestActions({ status: "RESOLVED", task: null, work_order: null });
  assert.deepEqual(resolved, { acknowledge: false, toTask: false, toMaintenance: false, resolve: false, close: true, cancel: false });
  const closed = requestActions({ status: "CLOSED", task: null, work_order: null });
  assert.ok(Object.values(closed).every((v) => v === false));
});

test("mobile layout shows one pane at a time", () => {
  assert.deepEqual(visiblePanes(false, "narrow"), { list: true, thread: false, context: false });
  assert.deepEqual(visiblePanes(true, "narrow"), { list: false, thread: true, context: false });
  assert.deepEqual(visiblePanes(true, "medium"), { list: true, thread: true, context: false });
  assert.deepEqual(visiblePanes(true, "wide"), { list: true, thread: true, context: true });
});

test("portal URL keeps the token in the fragment", () => {
  assert.equal(portalUrl("https://app.stayops.dev/", "/guest#token=abc"), "https://app.stayops.dev/guest#token=abc");
});
