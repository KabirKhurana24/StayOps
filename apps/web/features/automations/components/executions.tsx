"use client";

import type { ExecutionStatus } from "@stayops/shared";
import { PlayCircle, RotateCw, SearchX } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/form";
import { DescriptionItem, DescriptionList } from "@/components/ui/panel";
import { Segmented } from "@/components/ui/segmented";
import { Sheet } from "@/components/ui/sheet";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Table, TableScroll, Td, Th, THead, Tr } from "@/components/ui/table";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { ApiError } from "@/lib/api/client";
import { formatClock, formatRelative, plural } from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useSearchState } from "@/lib/use-search-state";
import { cn } from "@/lib/utils";

import { useCatalog, useExecution, useExecutions, useRetryExecution } from "../api";
import { entityHref, formatDuration, skippedSteps, timeline, type Tone } from "../model";
import { ApprovalCard } from "./approvals";
import { ExecutionStatusBadge, TriggerLabel } from "./parts";

const FILTERS = ["all", "failed", "waiting", "completed"] as const;
const FILTER_STATUS: Record<(typeof FILTERS)[number], ExecutionStatus[] | undefined> = {
  all: undefined,
  failed: ["FAILED"],
  waiting: ["WAITING_APPROVAL"],
  completed: ["COMPLETED"],
};

export function ExecutionsTab({ onOpen, workflowId }: { onOpen: (id: string) => void; workflowId?: string }) {
  const { get, set } = useSearchState();
  const now = useNow();
  const catalog = useCatalog();
  const filter = ((FILTERS as readonly string[]).includes(get("runs")) ? get("runs") : "all") as (typeof FILTERS)[number];
  const page = Math.max(0, Number(get("page")) || 0);
  const runs = useExecutions({ workflow_id: workflowId, status: FILTER_STATUS[filter], limit: 50, offset: page * 50 });
  return (
    <div className="overflow-hidden rounded-lg border bg-surface shadow-xs" data-testid="execution-history">
      <div className="border-b px-3 py-2.5">
        <Segmented
          label="Filter runs"
          size="sm"
          value={filter}
          onChange={(v) => set({ runs: v === "all" ? null : v, page: null })}
          options={[
            { value: "all", label: "All" },
            { value: "failed", label: "Failed" },
            { value: "waiting", label: "Waiting for approval" },
            { value: "completed", label: "Completed" },
          ]}
        />
      </div>
      {runs.isPending ? (
        <SkeletonRows rows={6} columns={6} />
      ) : runs.error ? (
        <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
      ) : runs.data.items.length === 0 ? (
        <EmptyState icon={PlayCircle} title={filter === "failed" ? "No failed runs" : "No runs yet"} description="Runs are recorded when a published workflow's trigger fires." />
      ) : (
        <>
          <TableScroll>
            <Table>
              <THead>
                <tr>
                  {workflowId ? null : <Th>Workflow</Th>}
                  <Th>Trigger</Th>
                  <Th>Affected</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Actions</Th>
                  <Th className="text-right">Retries</Th>
                  <Th>Started</Th>
                  <Th className="text-right">Duration</Th>
                </tr>
              </THead>
              <tbody>
                {runs.data.items.map((e) => (
                  <Tr key={e.id} interactive onClick={() => onOpen(e.id)} data-testid="execution-row" data-status={e.status}>
                    {workflowId ? null : (
                      <Td className="max-w-[220px]">
                        <span className="block truncate font-medium">{e.workflow_name}</span>
                        <span className="text-xs text-muted-foreground">v{e.workflow_version}</span>
                      </Td>
                    )}
                    <Td>
                      <TriggerLabel type={e.trigger_type} catalog={catalog.data} />
                    </Td>
                    <Td className="max-w-[220px] truncate text-[13px]">{e.entity_label ?? "—"}</Td>
                    <Td>
                      <ExecutionStatusBadge status={e.status} />
                    </Td>
                    <Td className="text-right tabular">{e.actions_executed}</Td>
                    <Td className="text-right tabular">{e.retry_count || "—"}</Td>
                    <Td className="text-xs whitespace-nowrap text-muted-foreground">{formatRelative(e.started_at, new Date(now))}</Td>
                    <Td className="text-right text-xs text-muted-foreground tabular">{formatDuration(e.duration_ms)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableScroll>
          <div className="flex items-center justify-between border-t px-4 py-2.5 text-xs text-muted-foreground">
            <span>{plural(runs.data.total, "run")}</span>
            {runs.data.total > 50 ? (
              <div className="flex gap-1.5">
                <Button size="sm" disabled={page === 0} onClick={() => set({ page: page > 1 ? String(page - 1) : null })}>
                  Previous
                </Button>
                <Button size="sm" disabled={(page + 1) * 50 >= runs.data.total} onClick={() => set({ page: String(page + 1) })}>
                  Next
                </Button>
              </div>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}

const DOT: Record<Tone, string> = {
  neutral: "bg-subtle-foreground",
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  accent: "bg-accent",
  outline: "bg-border-strong",
};

export function ExecutionSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  return (
    <Sheet open={Boolean(id)} onOpenChange={(o) => !o && onClose()} title="Workflow run" size="lg">
      {id ? <ExecutionDetailView id={id} /> : null}
    </Sheet>
  );
}

function ExecutionDetailView({ id }: { id: string }) {
  const run = useExecution(id);
  const org = useCurrentOrganization();
  const retry = useRetryExecution(id);
  const catalog = useCatalog();
  if (run.isPending) return <SkeletonRows rows={8} />;
  if (run.error) {
    return run.error instanceof ApiError && run.error.status === 404 ? (
      <EmptyState icon={SearchX} title="Run not found" />
    ) : (
      <ErrorState error={run.error} onRetry={() => run.refetch()} />
    );
  }
  const d = run.data;
  const href = entityHref(d.entity_type, d.entity_id);
  const entries = timeline(d);
  const skipped = skippedSteps(d);
  const failedStep = d.steps.find((s) => s.status === "FAILED");
  return (
    <div className="space-y-5" data-testid="execution-detail" data-status={d.status}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <Link href={`/automations/${d.workflow_id}`} className="text-[15px] font-semibold hover:underline">
            {d.workflow_name}
          </Link>
          <span className="ml-2 text-xs text-muted-foreground">v{d.workflow_version}</span>
        </div>
        <ExecutionStatusBadge status={d.status} />
      </div>
      <DescriptionList className="rounded-lg border">
        <DescriptionItem label="Trigger">
          <TriggerLabel type={d.trigger_type} catalog={catalog.data} />
        </DescriptionItem>
        <DescriptionItem label="Affected">
          {href ? (
            <Link href={href} className="text-accent hover:underline">
              {d.entity_label}
            </Link>
          ) : (
            (d.entity_label ?? "—")
          )}
        </DescriptionItem>
        <DescriptionItem label="Duration">{formatDuration(d.duration_ms)}</DescriptionItem>
        <DescriptionItem label="Actions">{plural(d.actions_executed, "action")} performed</DescriptionItem>
        {d.retry_count ? (
          <DescriptionItem label="Retries">
            {d.retry_count}
            {d.last_retry_at ? ` · last ${formatClock(d.last_retry_at)}` : null}
          </DescriptionItem>
        ) : null}
      </DescriptionList>

      {d.status === "FAILED" ? (
        <FormError message={d.error_summary ?? "This run failed."}>
          {failedStep?.error_code ? <p className="mt-1 font-mono text-[11px]">code: {failedStep.error_code}</p> : null}
          {d.can_retry && can(org.data, "automations:manage") ? (
            <div className="mt-2">
              <Button size="sm" onClick={() => retry.mutate()} disabled={retry.isPending} data-testid="retry-execution">
                <RotateCw /> {retry.isPending ? "Retrying…" : "Retry from the failed step"}
              </Button>
              <p className="mt-1 text-xs text-muted-foreground">Completed steps won’t run again.</p>
            </div>
          ) : null}
        </FormError>
      ) : null}

      {d.approvals.length ? (
        <section className="space-y-2">
          <h3 className="eyebrow">Approvals</h3>
          {d.approvals.map((a) => (
            <ApprovalCard key={a.id} approval={a} compact />
          ))}
        </section>
      ) : null}

      <section>
        <h3 className="eyebrow mb-2">Timeline</h3>
        <ol className="relative space-y-3 border-l pl-4" data-testid="execution-timeline">
          {entries.map((e) => (
            <li key={e.key} className="relative" data-testid="timeline-entry" data-kind={e.kind}>
              <span className={cn("absolute top-1.5 -left-[21px] size-2.5 rounded-full ring-2 ring-surface", DOT[e.tone])} aria-hidden />
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[13px] font-medium">{e.title}</span>
                {e.at ? (
                  <time className="shrink-0 font-mono text-[11px] text-subtle-foreground" dateTime={e.at}>
                    {formatClock(e.at)}
                  </time>
                ) : null}
              </div>
              {e.detail ? <p className="mt-0.5 text-xs text-muted-foreground">{e.detail}</p> : null}
            </li>
          ))}
        </ol>
      </section>

      {skipped.length ? (
        <section>
          <h3 className="eyebrow mb-2">Skipped</h3>
          <ul className="space-y-1">
            {skipped.map((s) => (
              <li key={s.id} className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>{s.label}</span>
                <Badge tone="outline">{String((s.output_summary as { reason?: string }).reason ?? "Skipped")}</Badge>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <details className="rounded-lg border px-3 py-2 text-xs">
        <summary className="cursor-pointer font-medium text-muted-foreground">Data this run used</summary>
        <dl className="mt-2 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-x-3 gap-y-1">
          {Object.entries(d.fields).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="truncate font-mono text-subtle-foreground">{k}</dt>
              <dd className="truncate">{v === null ? "—" : String(v)}</dd>
            </div>
          ))}
        </dl>
      </details>
    </div>
  );
}
