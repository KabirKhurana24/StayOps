"use client";

import type { AutomationApproval } from "@stayops/shared";
import { Check, ShieldCheck, X } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/form";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { formatRelative } from "@/lib/format";
import { useNow } from "@/lib/use-now";

import { useApprovals, useDecideApproval } from "../api";
import { entityHref } from "../model";
import { ApprovalStatusBadge, RiskBadge } from "./parts";

export function ApprovalsTab({ onOpenRun }: { onOpenRun: (id: string) => void }) {
  const pending = useApprovals({ status: ["PENDING"] });
  const decided = useApprovals({ status: ["EXECUTED", "REJECTED", "FAILED", "CANCELLED"], limit: 20 });
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <section aria-label="Waiting for approval" data-testid="approval-queue">
        <h2 className="mb-2 text-[13px] font-semibold">Waiting for a decision</h2>
        {pending.isPending ? (
          <SkeletonRows rows={3} />
        ) : pending.error ? (
          <ErrorState error={pending.error} onRetry={() => pending.refetch()} />
        ) : pending.data.items.length === 0 ? (
          <div className="rounded-lg border bg-surface">
            <EmptyState compact icon={ShieldCheck} title="Nothing waiting" description="Actions that need a human decision appear here, and in the notification bell." />
          </div>
        ) : (
          <div className="space-y-3">
            {pending.data.items.map((a) => (
              <ApprovalCard key={a.id} approval={a} onOpenRun={onOpenRun} />
            ))}
          </div>
        )}
      </section>
      <section aria-label="Recent decisions">
        <h2 className="mb-2 text-[13px] font-semibold">Recent decisions</h2>
        <div className="space-y-2">
          {(decided.data?.items ?? []).map((a) => (
            <ApprovalCard key={a.id} approval={a} compact onOpenRun={onOpenRun} />
          ))}
          {decided.data && decided.data.items.length === 0 ? <p className="text-xs text-muted-foreground">No decisions yet.</p> : null}
        </div>
      </section>
    </div>
  );
}

export function ApprovalCard({
  approval: a,
  compact = false,
  onOpenRun,
}: {
  approval: AutomationApproval;
  compact?: boolean;
  onOpenRun?: (id: string) => void;
}) {
  const org = useCurrentOrganization();
  const decide = useDecideApproval(a.id);
  const now = useNow();
  const canDecide = can(org.data, "automations:approve") && a.status === "PENDING";
  const href = entityHref(a.entity_type, a.entity_id);
  return (
    <article className="rounded-lg border bg-surface p-3.5 shadow-xs" data-testid="automation-approval" data-status={a.status}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[13px] font-semibold">{a.title}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {a.workflow_name ? (
              <Link href={`/automations/${a.workflow_id}`} className="hover:underline">
                {a.workflow_name}
              </Link>
            ) : null}
            {" · "}
            {formatRelative(a.created_at, new Date(now))}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <RiskBadge risk={a.risk} />
          <ApprovalStatusBadge status={a.status} />
        </div>
      </div>
      {!compact ? (
        <dl className="mt-3 grid gap-1.5 text-xs">
          <div>
            <dt className="inline font-medium text-foreground">Why: </dt>
            <dd className="inline text-muted-foreground">{a.reason}</dd>
          </div>
          <div>
            <dt className="inline font-medium text-foreground">Effect: </dt>
            <dd className="inline text-muted-foreground">{a.expected_effect}</dd>
          </div>
          {a.entity_label ? (
            <div>
              <dt className="inline font-medium text-foreground">Target: </dt>
              <dd className="inline">
                {href ? (
                  <Link href={href} className="text-accent hover:underline">
                    {a.entity_label}
                  </Link>
                ) : (
                  a.entity_label
                )}
              </dd>
            </div>
          ) : null}
        </dl>
      ) : null}
      {a.decided_by_label ? (
        <p className="mt-2 text-xs text-muted-foreground">
          Decided by <span className="font-medium text-foreground">{a.decided_by_label}</span>
          {a.decided_at ? ` · ${formatRelative(a.decided_at, new Date(now))}` : null}
        </p>
      ) : null}
      {a.error ? <p className="mt-1.5 text-xs text-danger">{a.error}</p> : null}
      {decide.error ? (
        <div className="mt-2">
          <FormError message={decide.error.message} />
        </div>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        {onOpenRun ? (
          <Button size="xs" variant="link" onClick={() => onOpenRun(a.execution_id)}>
            View run
          </Button>
        ) : (
          <span />
        )}
        {canDecide ? (
          <div className="flex gap-1.5">
            <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate({ approve: false, version: a.version })}>
              <X /> Reject
            </Button>
            <Button size="sm" variant="primary" disabled={decide.isPending} onClick={() => decide.mutate({ approve: true, version: a.version })}>
              <Check /> {decide.isPending ? "Working…" : "Approve"}
            </Button>
          </div>
        ) : null}
      </div>
    </article>
  );
}
