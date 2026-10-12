import assert from "node:assert/strict";
import { test } from "node:test";

import type { ActionNode, CatalogAction, CatalogField, ConditionNode, WorkflowDefinition } from "@stayops/shared";

import {
  canConnect,
  connect,
  describeCondition,
  effectiveApproval,
  insertNode,
  layout,
  newNodeId,
  outline,
  removeNode,
} from "./graph.ts";

const action = (id: string, type = "create_operational_alert"): ActionNode => ({ id, kind: "action", type, config: { title: id } });
const cond = (id: string): ConditionNode => ({
  id,
  kind: "condition",
  condition: { match: "all", rules: [{ field: "task.priority", op: "eq", value: "URGENT" }] },
});
const base = (): WorkflowDefinition => ({
  schema_version: 1,
  nodes: [{ id: "trigger", kind: "trigger", type: "task.created", config: {} }],
  edges: [],
  settings: { on_error: "stop" },
});

test("inserting keeps the chain connected", () => {
  let d = insertNode(base(), { source: "trigger", branch: "next" }, action("a"));
  d = insertNode(d, { source: "trigger", branch: "next" }, cond("c"));
  // trigger → c → (yes) a
  assert.deepEqual(
    d.edges.map((e) => `${e.source}:${e.branch}:${e.target}`).sort(),
    ["c:true:a", "trigger:next:c"],
  );
  d = insertNode(d, { source: "c", branch: "false" }, action("b"));
  assert.equal(d.edges.length, 3);
});

test("connections that would loop, merge or misuse branches are refused", () => {
  let d = insertNode(base(), { source: "trigger", branch: "next" }, cond("c"));
  d = insertNode(d, { source: "c", branch: "true" }, action("a"));
  d = { ...d, nodes: [...d.nodes, action("orphan")] };
  assert.equal(canConnect(d, { source: "a", branch: "next" }, "trigger").ok, false);
  assert.equal(canConnect(d, { source: "a", branch: "next" }, "c").ok, false); // loop / already has parent
  assert.equal(canConnect(d, { source: "a", branch: "true" }, "orphan").ok, false); // actions don't branch
  assert.equal(canConnect(d, { source: "c", branch: "true" }, "orphan").ok, false); // branch taken
  assert.equal(canConnect(d, { source: "c", branch: "next" }, "orphan").ok, false); // conditions need yes/no
  assert.equal(canConnect(d, { source: "a", branch: "next" }, "a").ok, false);
  assert.equal(canConnect(d, { source: "c", branch: "false" }, "orphan").ok, true);
  const linked = connect(d, { source: "c", branch: "false" }, "orphan");
  assert.equal(canConnect(linked, { source: "a", branch: "next" }, "orphan").ok, false); // would merge
  assert.throws(() => connect(linked, { source: "orphan", branch: "next" }, "c"));
});

test("removing a step reconnects its parent and drops a condition's No branch", () => {
  let d = insertNode(base(), { source: "trigger", branch: "next" }, cond("c"));
  d = insertNode(d, { source: "c", branch: "true" }, action("yes1"));
  d = insertNode(d, { source: "yes1", branch: "next" }, action("yes2"));
  d = insertNode(d, { source: "c", branch: "false" }, action("no1"));
  const middle = removeNode(d, "yes1");
  assert.deepEqual(middle.removed, ["yes1"]);
  assert.ok(middle.definition.edges.some((e) => e.source === "c" && e.branch === "true" && e.target === "yes2"));
  const condRemoved = removeNode(d, "c");
  assert.deepEqual(new Set(condRemoved.removed), new Set(["c", "no1"]));
  assert.ok(condRemoved.definition.edges.some((e) => e.source === "trigger" && e.target === "yes1"));
  assert.throws(() => removeNode(d, "trigger"));
});

test("layout places parents above their children and lists orphans", () => {
  let d = insertNode(base(), { source: "trigger", branch: "next" }, cond("c"));
  d = insertNode(d, { source: "c", branch: "true" }, action("a"));
  d = insertNode(d, { source: "c", branch: "false" }, action("b"));
  d = { ...d, nodes: [...d.nodes, action("loose")] };
  const { placed, orphans, columns } = layout(d);
  const at = Object.fromEntries(placed.map((p) => [p.node.id, p]));
  assert.equal(columns, 2);
  assert.equal(at.trigger.depth, 0);
  assert.equal(at.c.column, (at.a.column + at.b.column) / 2);
  assert.equal(at.a.via, "true");
  assert.deepEqual(orphans.map((n) => n.id), ["loose"]);
  assert.deepEqual(outline(d).map((o) => `${o.node.id}@${o.depth}`), ["trigger@0", "c@0", "a@1", "b@1"]);
});

test("ids, descriptions and approval policy", () => {
  const d = insertNode(base(), { source: "trigger", branch: "next" }, action("action_1"));
  assert.equal(newNodeId(d, "action"), "action_2");
  const fields = new Map<string, CatalogField>([
    ["task.priority", { path: "task.priority", label: "Task priority", type: "enum", scope: "task", values: [], unit: null, ops: [] }],
  ]);
  assert.equal(describeCondition(cond("c"), fields), "Task priority is URGENT");
  const catalog = (over: Partial<CatalogAction>): CatalogAction => ({
    type: "send_in_app_message",
    label: "Send",
    category: "Guest experience",
    description: "",
    risk: "MODERATE",
    guest_facing: true,
    default_approval: "required",
    always_requires_approval: false,
    permissions: [],
    template_fields: [],
    member_fields: [],
    input_schema: {},
    ...over,
  });
  assert.equal(effectiveApproval(action("m", "send_in_app_message"), catalog({})), "required");
  assert.equal(effectiveApproval({ ...action("m", "send_in_app_message"), approval: "auto" }, catalog({})), "auto");
  assert.equal(effectiveApproval({ ...action("g", "request_human_approval"), approval: "auto" }, catalog({ always_requires_approval: true })), "required");
  assert.equal(
    effectiveApproval({ ...action("w", "create_maintenance_work_order"), config: { blocks_unit: true } }, catalog({ type: "create_maintenance_work_order", default_approval: "auto" })),
    "required",
  );
});
