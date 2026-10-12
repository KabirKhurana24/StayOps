/**
 * Pure editing operations on a workflow definition (no React, unit-tested).
 *
 * The model mirrors the server's rules: one trigger at the root, every other step
 * has at most one parent, actions continue on "next", conditions branch on
 * "true" / "false". Editing goes through these helpers so the canvas can't build a
 * cycle or a merge; the server validator remains the authority on publish.
 */
import type {
  ActionNode,
  Branch,
  CatalogAction,
  CatalogField,
  ConditionNode,
  Rule,
  RuleGroup,
  TriggerNode,
  WorkflowDefinition,
  WorkflowEdge,
  WorkflowNode,
} from "@stayops/shared";

export type Slot = { source: string; branch: Branch };

export const branchesOf = (node: WorkflowNode): Branch[] => (node.kind === "condition" ? ["true", "false"] : ["next"]);

export function nodeById(def: WorkflowDefinition, id: string): WorkflowNode | undefined {
  return def.nodes.find((n) => n.id === id);
}

export function triggerOf(def: WorkflowDefinition): TriggerNode | undefined {
  return def.nodes.find((n): n is TriggerNode => n.kind === "trigger");
}

export function childOf(def: WorkflowDefinition, source: string, branch: Branch): string | undefined {
  return def.edges.find((e) => e.source === source && e.branch === branch)?.target;
}

export function parentOf(def: WorkflowDefinition, id: string): WorkflowEdge | undefined {
  return def.edges.find((e) => e.target === id);
}

export function newNodeId(def: WorkflowDefinition, prefix: string): string {
  const taken = new Set(def.nodes.map((n) => n.id));
  for (let i = 1; ; i += 1) {
    const id = `${prefix}_${i}`;
    if (!taken.has(id)) return id;
  }
}

const edgeId = (source: string, target: string, branch: Branch) => `${source}-${branch}-${target}`;

/** Every node reachable from `id` (including itself). Bounded by the node count. */
export function descendants(def: WorkflowDefinition, id: string): Set<string> {
  const seen = new Set<string>();
  const stack = [id];
  while (stack.length && seen.size <= def.nodes.length) {
    const current = stack.pop() as string;
    if (seen.has(current)) continue;
    seen.add(current);
    for (const e of def.edges) if (e.source === current) stack.push(e.target);
  }
  return seen;
}

/** Can `source` (on `branch`) connect to `target` without breaking the tree? */
export function canConnect(def: WorkflowDefinition, slot: Slot, target: string): { ok: true } | { ok: false; reason: string } {
  const source = nodeById(def, slot.source);
  const dest = nodeById(def, target);
  if (!source || !dest) return { ok: false, reason: "That step doesn't exist." };
  if (dest.kind === "trigger") return { ok: false, reason: "Nothing can connect into the trigger." };
  if (slot.source === target) return { ok: false, reason: "A step can't connect to itself." };
  if (!branchesOf(source).includes(slot.branch)) {
    return { ok: false, reason: source.kind === "condition" ? "Conditions connect on Yes or No." : "Only conditions branch." };
  }
  if (childOf(def, slot.source, slot.branch)) return { ok: false, reason: "That branch already continues to a step." };
  if (parentOf(def, target)) return { ok: false, reason: "That step already follows another step." };
  if (descendants(def, target).has(slot.source)) return { ok: false, reason: "That would create a loop." };
  return { ok: true };
}

export function connect(def: WorkflowDefinition, slot: Slot, target: string): WorkflowDefinition {
  const check = canConnect(def, slot, target);
  if (!check.ok) throw new Error(check.reason);
  return { ...def, edges: [...def.edges, { id: edgeId(slot.source, target, slot.branch), source: slot.source, target, branch: slot.branch }] };
}

export function disconnect(def: WorkflowDefinition, target: string): WorkflowDefinition {
  return { ...def, edges: def.edges.filter((e) => e.target !== target) };
}

/**
 * Insert `node` into `slot`. Whatever followed the slot now follows the new node
 * (on "next" for actions, on "Yes" for conditions), so the chain stays connected.
 */
export function insertNode(def: WorkflowDefinition, slot: Slot, node: ActionNode | ConditionNode): WorkflowDefinition {
  if (!nodeById(def, slot.source)) throw new Error("Unknown step.");
  const following = childOf(def, slot.source, slot.branch);
  const edges = def.edges.filter((e) => !(e.source === slot.source && e.branch === slot.branch));
  edges.push({ id: edgeId(slot.source, node.id, slot.branch), source: slot.source, target: node.id, branch: slot.branch });
  if (following) {
    const branch: Branch = node.kind === "condition" ? "true" : "next";
    edges.push({ id: edgeId(node.id, following, branch), source: node.id, target: following, branch });
  }
  return { ...def, nodes: [...def.nodes, node], edges };
}

/**
 * Remove a step. Its parent reconnects to the step's main continuation ("next",
 * or a condition's "Yes" branch); a condition's "No" branch is removed with it.
 * Returns the ids that were removed (so the UI can confirm larger deletions).
 */
export function removeNode(def: WorkflowDefinition, id: string): { definition: WorkflowDefinition; removed: string[] } {
  const node = nodeById(def, id);
  if (!node) return { definition: def, removed: [] };
  if (node.kind === "trigger") throw new Error("The trigger can't be removed — change its type instead.");
  const parent = parentOf(def, id);
  const keep = node.kind === "condition" ? childOf(def, id, "true") : childOf(def, id, "next");
  const dropped = node.kind === "condition" ? childOf(def, id, "false") : undefined;
  const removed = new Set<string>([id, ...(dropped ? descendants(def, dropped) : [])]);
  let edges = def.edges.filter((e) => !removed.has(e.source) && !removed.has(e.target));
  edges = edges.filter((e) => !(e.source === id || e.target === id));
  if (parent && keep && !removed.has(keep)) {
    edges.push({ id: edgeId(parent.source, keep, parent.branch), source: parent.source, target: keep, branch: parent.branch });
  }
  return { definition: { ...def, nodes: def.nodes.filter((n) => !removed.has(n.id)), edges }, removed: [...removed] };
}

export function updateNode(def: WorkflowDefinition, id: string, patch: (node: WorkflowNode) => WorkflowNode): WorkflowDefinition {
  return { ...def, nodes: def.nodes.map((n) => (n.id === id ? patch(n) : n)) };
}

// ---------------------------------------------------------------------- layout

export type Placed = { node: WorkflowNode; depth: number; column: number; via: Branch | null };

/**
 * Tidy tree layout: rows by depth, columns by leaf order (a parent sits above the
 * middle of its children). Nodes not connected to the trigger are listed last.
 */
export function layout(def: WorkflowDefinition): { placed: Placed[]; orphans: WorkflowNode[]; columns: number } {
  const root = triggerOf(def);
  const placed: Placed[] = [];
  let nextLeaf = 0;
  const visit = (id: string, depth: number, via: Branch | null, seen: Set<string>): number => {
    const node = nodeById(def, id);
    if (!node || seen.has(id) || depth > def.nodes.length) return nextLeaf;
    seen.add(id);
    const children = branchesOf(node)
      .map((b) => ({ b, target: childOf(def, id, b) }))
      .filter((c): c is { b: Branch; target: string } => Boolean(c.target));
    let column: number;
    if (children.length === 0) {
      column = nextLeaf;
      nextLeaf += 1;
    } else {
      const cols = children.map((c) => visit(c.target, depth + 1, c.b, seen));
      column = (Math.min(...cols) + Math.max(...cols)) / 2;
    }
    placed.push({ node, depth, column, via });
    return column;
  };
  const seen = new Set<string>();
  if (root) visit(root.id, 0, null, seen);
  const orphans = def.nodes.filter((n) => !seen.has(n.id));
  return { placed: placed.sort((a, b) => a.depth - b.depth || a.column - b.column), orphans, columns: Math.max(nextLeaf, 1) };
}

/** Steps in run order along each path (mobile / read-only view). */
export function outline(def: WorkflowDefinition): { node: WorkflowNode; depth: number; via: Branch | null }[] {
  const out: { node: WorkflowNode; depth: number; via: Branch | null }[] = [];
  const walk = (id: string, depth: number, via: Branch | null, guard: number) => {
    const node = nodeById(def, id);
    if (!node || guard > def.nodes.length) return;
    out.push({ node, depth, via });
    for (const b of branchesOf(node)) {
      const child = childOf(def, id, b);
      if (child) walk(child, node.kind === "condition" ? depth + 1 : depth, node.kind === "condition" ? b : null, guard + 1);
    }
  };
  const root = triggerOf(def);
  if (root) walk(root.id, 0, null, 0);
  return out;
}

// --------------------------------------------------------------------- helpers

export function isRuleGroup(rule: Rule | RuleGroup): rule is RuleGroup {
  return (rule as RuleGroup).rules !== undefined;
}

export const OP_TEXT: Record<string, string> = {
  eq: "is",
  neq: "is not",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  in: "is one of",
  not_in: "is none of",
  exists: "is set",
  not_exists: "is not set",
};

export function describeRule(rule: Rule | RuleGroup, fields: Map<string, CatalogField>): string {
  if (isRuleGroup(rule)) {
    return `(${rule.rules.map((r) => describeRule(r, fields)).join(rule.match === "all" ? " and " : " or ")})`;
  }
  const label = fields.get(rule.field)?.label ?? rule.field;
  const value =
    rule.op === "exists" || rule.op === "not_exists"
      ? ""
      : ` ${Array.isArray(rule.value) ? rule.value.join(", ") : typeof rule.value === "boolean" ? (rule.value ? "yes" : "no") : String(rule.value ?? "…")}`;
  return `${label} ${OP_TEXT[rule.op] ?? rule.op}${value}`;
}

export function describeCondition(node: ConditionNode, fields: Map<string, CatalogField>): string {
  const { rules, match } = node.condition;
  if (!rules.length) return "No rules yet";
  return rules.map((r) => describeRule(r, fields)).join(match === "all" ? " and " : " or ");
}

/** The approval policy the server will apply (mirrors validation.approval_policy). */
export function effectiveApproval(node: ActionNode, action: CatalogAction | undefined): "auto" | "required" {
  if (!action || action.always_requires_approval) return "required";
  if (node.approval) return node.approval;
  if (action.type === "create_maintenance_work_order") return node.config.blocks_unit ? "required" : "auto";
  if (action.type === "mark_guest_request_acknowledged") return node.config.notify_guest === false ? "auto" : "required";
  return action.default_approval;
}

/** Placeholder paths available to a trigger (for template text inputs). */
export function placeholderFields(fields: CatalogField[], scopes: string[]): CatalogField[] {
  const allowed = new Set(scopes);
  return fields.filter((f) => allowed.has(f.scope));
}

export function sameDefinition(a: WorkflowDefinition, b: WorkflowDefinition): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}
