"use client";

import type { AutomationCatalog, CatalogField, ValidationIssue, WorkflowDefinition, WorkflowNode } from "@stayops/shared";
import { AlertCircle, CalendarClock, GitBranch, Plus, ShieldQuestion, Zap } from "lucide-react";
import { useMemo } from "react";

import { cn } from "@/lib/utils";

import { branchesOf, childOf, describeCondition, effectiveApproval, layout, outline, type Slot } from "../graph";

const NODE_W = 236;
const NODE_H = 76;
const COL_W = 268;
const ROW_H = 132;
const PAD = 24;

export function nodeTitle(node: WorkflowNode, catalog: AutomationCatalog): string {
  if (node.kind === "trigger") return catalog.triggers.find((t) => t.type === node.type)?.label ?? node.type;
  if (node.kind === "condition") return node.label || "Condition";
  return node.label || catalog.actions.find((a) => a.type === node.type)?.label || node.type;
}

function subtitle(node: WorkflowNode, catalog: AutomationCatalog, fields: Map<string, CatalogField>): string {
  if (node.kind === "trigger") return catalog.triggers.find((t) => t.type === node.type)?.kind === "schedule" ? "Scheduled trigger" : "When this happens";
  if (node.kind === "condition") return describeCondition(node, fields);
  const title = node.config.title ?? node.config.content ?? node.config.note;
  return typeof title === "string" ? title : (catalog.actions.find((a) => a.type === node.type)?.description ?? "");
}

export function WorkflowCanvas({
  definition,
  catalog,
  issues,
  selected,
  slot,
  readOnly,
  onSelect,
  onSlot,
}: {
  definition: WorkflowDefinition;
  catalog: AutomationCatalog;
  issues: ValidationIssue[];
  selected: string | null;
  slot: Slot | null;
  readOnly: boolean;
  onSelect: (id: string) => void;
  onSlot: (slot: Slot) => void;
}) {
  const fields = useMemo(() => new Map(catalog.fields.map((f) => [f.path, f])), [catalog.fields]);
  const { placed, orphans, columns } = useMemo(() => layout(definition), [definition]);
  const depth = placed.reduce((m, p) => Math.max(m, p.depth), 0);
  const pos = new Map(placed.map((p) => [p.node.id, { x: PAD + p.column * COL_W, y: PAD + p.depth * ROW_H }]));
  orphans.forEach((n, i) => pos.set(n.id, { x: PAD + i * COL_W, y: PAD + (depth + 1) * ROW_H + 28 }));
  const width = PAD * 2 + Math.max(columns, orphans.length, 1) * COL_W;
  const height = PAD * 2 + (depth + 1) * ROW_H + (orphans.length ? ROW_H + 28 : 0);
  const issueIds = new Set(issues.map((i) => i.node_id).filter(Boolean));

  return (
    <div className="scrollbar-thin relative h-full min-h-[420px] overflow-auto bg-[radial-gradient(var(--color-border)_1px,transparent_1px)] [background-size:16px_16px]" data-testid="workflow-canvas">
      <div className="relative" style={{ width, height }}>
        <svg className="pointer-events-none absolute inset-0" width={width} height={height} aria-hidden>
          {definition.edges.map((e) => {
            const a = pos.get(e.source);
            const b = pos.get(e.target);
            if (!a || !b) return null;
            const x1 = a.x + NODE_W / 2;
            const y1 = a.y + NODE_H;
            const x2 = b.x + NODE_W / 2;
            const y2 = b.y;
            const mid = (y1 + y2) / 2;
            return (
              <g key={`${e.source}-${e.branch}-${e.target}`}>
                <path d={`M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`} fill="none" className={e.branch === "false" ? "stroke-border-strong" : "stroke-accent/60"} strokeWidth={1.5} strokeDasharray={e.branch === "false" ? "4 3" : undefined} />
                {e.branch !== "next" ? (
                  <text x={x2 + 6} y={y2 - 8} className="fill-muted-foreground text-[10px] font-medium">
                    {e.branch === "true" ? "Yes" : "No"}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
        {orphans.length ? (
          <div className="absolute text-[11px] font-medium text-warning" style={{ left: PAD, top: PAD + (depth + 1) * ROW_H + 6 }}>
            Not connected — select a step to connect it
          </div>
        ) : null}
        {[...placed.map((p) => p.node), ...orphans].map((node) => {
          const p = pos.get(node.id);
          if (!p) return null;
          const action = node.kind === "action" ? catalog.actions.find((a) => a.type === node.type) : undefined;
          const needsApproval = node.kind === "action" && effectiveApproval(node, action) === "required";
          const Icon = node.kind === "trigger" ? (catalog.triggers.find((t) => t.type === node.type)?.kind === "schedule" ? CalendarClock : Zap) : node.kind === "condition" ? GitBranch : Plus;
          return (
            <div key={node.id} className="absolute" style={{ left: p.x, top: p.y, width: NODE_W }}>
              <button
                type="button"
                onClick={() => onSelect(node.id)}
                data-testid="node-card"
                data-node-id={node.id}
                data-kind={node.kind}
                aria-pressed={selected === node.id}
                className={cn(
                  "flex h-[76px] w-full flex-col justify-center gap-0.5 rounded-lg border bg-surface px-3 text-left shadow-xs transition-[border-color,box-shadow] hover:border-border-strong",
                  node.kind === "trigger" && "border-accent/40",
                  selected === node.id && "border-accent ring-[3px] ring-accent/15",
                  issueIds.has(node.id) && "border-danger/60",
                )}
              >
                <span className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                  {node.kind === "action" ? (
                    <span className="grid size-3.5 place-items-center rounded-[3px] bg-accent-soft text-accent">
                      <Zap className="size-2.5" />
                    </span>
                  ) : (
                    <Icon className="size-3.5" aria-hidden />
                  )}
                  {node.kind === "trigger" ? "Trigger" : node.kind === "condition" ? "Condition" : (action?.category ?? "Action")}
                  {needsApproval ? <ShieldQuestion className="ml-auto size-3.5 text-warning" aria-label="Needs approval" /> : null}
                  {issueIds.has(node.id) ? <AlertCircle className={cn("size-3.5 text-danger", !needsApproval && "ml-auto")} aria-label="Has problems" /> : null}
                </span>
                <span className="truncate text-[13px] font-semibold">{nodeTitle(node, catalog)}</span>
                <span className="truncate text-xs text-muted-foreground">{subtitle(node, catalog, fields)}</span>
              </button>
              {!readOnly ? (
                <div className={cn("mt-1 flex gap-1", node.kind === "condition" ? "justify-end pr-2" : "justify-center")}>
                  {branchesOf(node)
                    .filter((b) => !childOf(definition, node.id, b))
                    .map((b) => {
                      const active = slot?.source === node.id && slot.branch === b;
                      const label = b === "next" ? "" : b === "true" ? "Yes" : "No";
                      return (
                        <button
                          key={b}
                          type="button"
                          onClick={() => onSlot({ source: node.id, branch: b })}
                          aria-label={`Add step after ${nodeTitle(node, catalog)}${label ? ` (${label})` : ""}`}
                          className={cn(
                            "flex h-5 items-center gap-0.5 rounded-full border bg-surface px-1.5 text-[10px] font-medium text-muted-foreground shadow-xs hover:border-accent hover:text-accent",
                            active && "border-accent bg-accent-soft text-accent",
                          )}
                        >
                          <Plus className="size-3" /> {label}
                        </button>
                      );
                    })}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Readable stacked view of the workflow for phones and narrow tablets. */
export function WorkflowOutline({ definition, catalog, onSelect }: { definition: WorkflowDefinition; catalog: AutomationCatalog; onSelect?: (id: string) => void }) {
  const fields = useMemo(() => new Map(catalog.fields.map((f) => [f.path, f])), [catalog.fields]);
  return (
    <ol className="space-y-2" data-testid="workflow-outline">
      {outline(definition).map(({ node, depth, via }) => (
        <li key={node.id} style={{ marginLeft: depth * 16 }}>
          <button type="button" onClick={() => onSelect?.(node.id)} className="w-full rounded-lg border bg-surface px-3 py-2 text-left shadow-xs">
            <span className="block text-[11px] font-medium text-muted-foreground uppercase">
              {via ? `${via === "true" ? "If yes" : "If no"} · ` : ""}
              {node.kind}
            </span>
            <span className="block text-[13px] font-semibold">{nodeTitle(node, catalog)}</span>
            <span className="block text-xs text-muted-foreground">{subtitle(node, catalog, fields)}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}
