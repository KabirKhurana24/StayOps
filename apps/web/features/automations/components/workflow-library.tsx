"use client";

import type { WorkflowStatus } from "@stayops/shared";
import { Workflow as WorkflowIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useDeferredValue } from "react";

import { Badge } from "@/components/ui/badge";
import { SearchInput } from "@/components/ui/form";
import { Segmented } from "@/components/ui/segmented";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Table, TableScroll, Td, Th, THead, Tr } from "@/components/ui/table";
import { formatRelative, plural } from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useSearchState } from "@/lib/use-search-state";

import { useCatalog, useWorkflows } from "../api";
import { successRate } from "../model";
import { ExecutionStatusBadge, TriggerLabel, WorkflowStatusBadge } from "./parts";

const FILTERS = ["all", "active", "paused", "draft", "failed"] as const;
type Filter = (typeof FILTERS)[number];
const FILTER_STATUS: Partial<Record<Filter, WorkflowStatus>> = { active: "ACTIVE", paused: "PAUSED", draft: "DRAFT" };

export function WorkflowLibrary() {
  const { get, set } = useSearchState();
  const router = useRouter();
  const now = useNow();
  const catalog = useCatalog();
  const filter = ((FILTERS as readonly string[]).includes(get("filter")) ? get("filter") : "all") as Filter;
  const q = useDeferredValue(get("q").trim());
  const status = FILTER_STATUS[filter];
  const workflows = useWorkflows({ status: status ? [status] : undefined, failed_recently: filter === "failed", q: q || undefined });

  return (
    <div className="overflow-hidden rounded-lg border bg-surface shadow-xs" data-testid="workflow-library">
      <div className="flex flex-col gap-2 border-b px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
        <Segmented
          label="Filter workflows"
          size="sm"
          value={filter}
          onChange={(v) => set({ filter: v === "all" ? null : v })}
          options={[
            { value: "all", label: "All" },
            { value: "active", label: "Active" },
            { value: "paused", label: "Paused" },
            { value: "draft", label: "Draft" },
            { value: "failed", label: "Failed recently" },
          ]}
        />
        <SearchInput
          aria-label="Search workflows"
          placeholder="Search workflows…"
          wrapperClassName="sm:w-64"
          value={get("q")}
          onChange={(e) => set({ q: e.target.value })}
          onClear={() => set({ q: null })}
        />
      </div>
      {workflows.isPending ? (
        <SkeletonRows rows={5} columns={6} />
      ) : workflows.error ? (
        <ErrorState error={workflows.error} onRetry={() => workflows.refetch()} />
      ) : workflows.data.items.length === 0 ? (
        <EmptyState
          icon={WorkflowIcon}
          title={filter === "all" && !q ? "No workflows yet" : "No workflows match"}
          description={
            filter === "all" && !q
              ? "Start from a template, describe one to the AI, or build one step by step. Nothing runs until you publish."
              : "Try another filter."
          }
        />
      ) : (
        <>
          <TableScroll>
            <Table>
              <THead>
                <tr>
                  <Th>Workflow</Th>
                  <Th>Status</Th>
                  <Th>Trigger</Th>
                  <Th>Last run</Th>
                  <Th className="text-right">Success</Th>
                  <Th className="text-right">Failures</Th>
                  <Th className="text-right">Version</Th>
                  <Th>Updated</Th>
                </tr>
              </THead>
              <tbody>
                {workflows.data.items.map((w) => (
                  <Tr key={w.id} interactive onClick={() => router.push(`/automations/${w.id}`)} data-testid="workflow-row" data-name={w.name}>
                    <Td className="max-w-[320px]">
                      <a href={`/automations/${w.id}`} onClick={(e) => e.preventDefault()} className="block truncate font-medium">
                        {w.name}
                      </a>
                      <span className="block truncate text-xs text-muted-foreground">{w.description ?? "No description"}</span>
                    </Td>
                    <Td>
                      <div className="flex items-center gap-1.5">
                        <WorkflowStatusBadge status={w.status} />
                        {w.has_unpublished_changes && w.current_version ? <Badge tone="outline">Unpublished edits</Badge> : null}
                      </div>
                    </Td>
                    <Td>
                      <TriggerLabel type={w.published_trigger_type ?? w.trigger_type} catalog={catalog.data} />
                    </Td>
                    <Td>
                      {w.stats.last_execution_at && w.stats.last_execution_status ? (
                        <div className="flex items-center gap-2">
                          <ExecutionStatusBadge status={w.stats.last_execution_status} />
                          <span className="text-xs text-muted-foreground">{formatRelative(w.stats.last_execution_at, new Date(now))}</span>
                        </div>
                      ) : (
                        <span className="text-xs text-subtle-foreground">Never</span>
                      )}
                    </Td>
                    <Td className="text-right tabular">{successRate(w.stats.success_rate)}</Td>
                    <Td className={w.stats.failed ? "text-right text-danger tabular" : "text-right tabular"}>{w.stats.failed}</Td>
                    <Td className="text-right tabular">{w.current_version ? `v${w.current_version}` : "—"}</Td>
                    <Td className="text-xs whitespace-nowrap text-muted-foreground">{formatRelative(w.updated_at, new Date(now))}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableScroll>
          <div className="border-t px-4 py-2.5 text-xs text-muted-foreground">{plural(workflows.data.total, "workflow")}</div>
        </>
      )}
    </div>
  );
}
