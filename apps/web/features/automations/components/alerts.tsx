"use client";

import type { AlertStatus, OperationalAlert } from "@stayops/shared";
import { BellRing } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { formatRelative } from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useSearchState } from "@/lib/use-search-state";

import { useAlertAction, useAlerts } from "../api";
import { entityHref } from "../model";
import { SeverityBadge } from "./parts";

const VIEWS: Record<string, AlertStatus[]> = { open: ["OPEN"], acknowledged: ["ACKNOWLEDGED"], resolved: ["RESOLVED"] };

export function AlertsTab() {
  const { get, set } = useSearchState();
  const view = get("alerts") in VIEWS ? get("alerts") : "open";
  const alerts = useAlerts({ status: VIEWS[view] });
  return (
    <div className="overflow-hidden rounded-lg border bg-surface shadow-xs" data-testid="alerts">
      <div className="border-b px-3 py-2.5">
        <Segmented
          label="Alert status"
          size="sm"
          value={view}
          onChange={(v) => set({ alerts: v === "open" ? null : v })}
          options={[
            { value: "open", label: "Open" },
            { value: "acknowledged", label: "Acknowledged" },
            { value: "resolved", label: "Resolved" },
          ]}
        />
      </div>
      {alerts.isPending ? (
        <SkeletonRows rows={4} />
      ) : alerts.error ? (
        <ErrorState error={alerts.error} onRetry={() => alerts.refetch()} />
      ) : alerts.data.items.length === 0 ? (
        <EmptyState icon={BellRing} title={view === "open" ? "No open alerts" : "Nothing here"} description="Workflows raise alerts with the “Raise operational alert” action." />
      ) : (
        <ul className="divide-y">
          {alerts.data.items.map((a) => (
            <AlertRow key={a.id} alert={a} />
          ))}
        </ul>
      )}
    </div>
  );
}

function AlertRow({ alert: a }: { alert: OperationalAlert }) {
  const org = useCurrentOrganization();
  const act = useAlertAction(a.id);
  const now = useNow();
  const href = entityHref(a.entity_type, a.entity_id);
  const canManage = can(org.data, "tasks:manage");
  return (
    <li className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between" data-testid="alert-row" data-status={a.status}>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <SeverityBadge severity={a.severity} />
          <span className="truncate text-[13px] font-medium">{a.title}</span>
        </div>
        {a.detail ? <p className="mt-1 text-xs text-muted-foreground">{a.detail}</p> : null}
        <p className="mt-1 text-xs text-subtle-foreground">
          {a.created_by_label ?? "Autopilot"} · {formatRelative(a.created_at, new Date(now))}
          {href && a.entity_label ? (
            <>
              {" · "}
              <Link href={href} className="text-accent hover:underline">
                {a.entity_label}
              </Link>
            </>
          ) : null}
          {a.acknowledged_by_label ? ` · acknowledged by ${a.acknowledged_by_label}` : null}
        </p>
      </div>
      {canManage && a.status !== "RESOLVED" ? (
        <div className="flex shrink-0 gap-1.5">
          {a.status === "OPEN" ? (
            <Button size="sm" disabled={act.isPending} onClick={() => act.mutate("acknowledge")}>
              Acknowledge
            </Button>
          ) : null}
          <Button size="sm" disabled={act.isPending} onClick={() => act.mutate("resolve")}>
            Resolve
          </Button>
        </div>
      ) : null}
    </li>
  );
}
