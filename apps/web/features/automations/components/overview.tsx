"use client";

import type { AutomationOverview } from "@stayops/shared";
import type { UseQueryResult } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clock, PlayCircle, ShieldCheck, Workflow, Zap } from "lucide-react";
import Link from "next/link";

import { Metric, MetricStrip } from "@/components/ui/misc";
import { Panel } from "@/components/ui/panel";
import { EmptyState, ErrorState, SkeletonBlock, SkeletonRows } from "@/components/ui/states";
import { formatRelative, humanizeAction } from "@/lib/format";
import { useNow } from "@/lib/use-now";

import { useCatalog } from "../api";
import { entityHref, formatDuration } from "../model";
import { ExecutionStatusBadge, TriggerLabel } from "./parts";

export function OverviewTab({
  overview,
  onOpenRun,
}: {
  overview: UseQueryResult<AutomationOverview>;
  onOpenRun: (id: string) => void;
}) {
  const now = useNow();
  const catalog = useCatalog();
  if (overview.isPending) {
    return (
      <div className="space-y-4">
        <SkeletonBlock />
        <SkeletonRows rows={6} />
      </div>
    );
  }
  if (overview.error) return <ErrorState error={overview.error} onRetry={() => overview.refetch()} />;
  const o = overview.data;
  return (
    <div className="space-y-4" data-testid="autopilot-overview">
      <MetricStrip label="Autopilot today" columns={6}>
        <Metric label="Active workflows" value={o.active_workflows} icon={<Workflow />} sub={`${o.paused_workflows} paused · ${o.draft_workflows} drafts`} testId="metric-active-workflows" />
        <Metric label="Runs (24 h)" value={o.executions_today} icon={<PlayCircle />} testId="metric-executions" />
        <Metric label="Succeeded" value={o.succeeded_today} icon={<CheckCircle2 />} tone={o.succeeded_today ? "success" : undefined} />
        <Metric label="Failed" value={o.failed_today} icon={<AlertTriangle />} tone={o.failed_today ? "danger" : undefined} testId="metric-failed" />
        <Metric label="Waiting for approval" value={o.waiting_approval} icon={<Clock />} tone={o.waiting_approval ? "warning" : undefined} testId="metric-waiting" />
        <Metric label="Actions performed" value={o.actions_today} icon={<Zap />} sub="by Autopilot, last 24 h" />
      </MetricStrip>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Panel title="Automated actions" description="What happened, why, which workflow — and who approved it" icon={<ShieldCheck />}>
          {o.recent_actions.length === 0 ? (
            <EmptyState compact icon={Zap} title="No automated actions yet" description="When a published workflow acts, every change appears here with its reason." />
          ) : (
            <ul className="divide-y" data-testid="automated-actions">
              {o.recent_actions.map((a) => {
                const auto = (a.automation ?? {}) as Record<string, string | undefined>;
                const href = entityHref(a.entity_type, a.entity_id);
                const what = `${humanizeAction(a.action)}${a.title ? ` “${a.title}”` : ""}`;
                return (
                  <li key={a.id} className="px-4 py-2.5 text-[13px]" data-testid="automated-action">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="min-w-0">
                        {href ? (
                          <Link href={href} className="font-medium hover:underline">
                            {what}
                          </Link>
                        ) : (
                          <span className="font-medium">{what}</span>
                        )}
                      </span>
                      <time className="shrink-0 text-xs text-subtle-foreground" dateTime={a.occurred_at}>
                        {formatRelative(a.occurred_at, new Date(now))}
                      </time>
                    </div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      Workflow <span className="font-medium text-foreground">{auto.workflow_name ?? "—"}</span>
                      {auto.workflow_version ? ` v${auto.workflow_version}` : null}
                      {auto.approved_by ? <> · approved by <span className="font-medium text-foreground">{auto.approved_by}</span></> : <> · automatic</>}
                      {auto.execution_id ? (
                        <>
                          {" · "}
                          <button type="button" className="text-accent hover:underline" onClick={() => onOpenRun(auto.execution_id as string)}>
                            why?
                          </button>
                        </>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>

        <div className="space-y-4">
          <Panel title="Repeated failures" description="Workflows that failed twice or more this week" icon={<AlertTriangle />}>
            {o.repeated_failures.length === 0 ? (
              <EmptyState compact icon={CheckCircle2} title="No repeated failures" />
            ) : (
              <ul className="divide-y">
                {o.repeated_failures.map((f) => (
                  <li key={f.workflow_id} className="flex items-center justify-between px-4 py-2.5 text-[13px]">
                    <Link href={`/automations/${f.workflow_id}`} className="font-medium hover:underline">
                      {f.name}
                    </Link>
                    <span className="text-xs text-danger tabular">{f.failures} failures</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Recent runs" icon={<PlayCircle />}>
            {o.recent_executions.length === 0 ? (
              <EmptyState compact icon={PlayCircle} title="No runs yet" description="Publish a workflow; runs appear as soon as a matching event happens." />
            ) : (
              <ul className="divide-y">
                {o.recent_executions.map((e) => (
                  <li key={e.id}>
                    <button type="button" onClick={() => onOpenRun(e.id)} className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left hover:bg-muted/50">
                      <span className="min-w-0">
                        <span className="block truncate text-[13px] font-medium">{e.workflow_name}</span>
                        <span className="block truncate text-xs text-muted-foreground">
                          <TriggerLabel type={e.trigger_type} catalog={catalog.data} /> · {e.entity_label ?? "—"} · {formatDuration(e.duration_ms)}
                        </span>
                      </span>
                      <ExecutionStatusBadge status={e.status} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
