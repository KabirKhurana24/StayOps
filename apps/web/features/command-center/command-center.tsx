"use client";

import { History, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { LiveDot } from "@/components/ui/misc";
import { PageHeader, Panel } from "@/components/ui/panel";
import { EmptyState, ErrorState, Skeleton, SkeletonBlock, SkeletonRows } from "@/components/ui/states";
import { useCommandCenter } from "@/features/activity/api";
import { ActivityFeed } from "@/features/activity/components/activity-feed";
import { can, useCurrentOrganization, useMe } from "@/features/organizations/api";
import { useRealtime } from "@/features/realtime/realtime-provider";
import { ReservationFormSheet } from "@/features/reservations/components/reservation-form";
import { firstName, greeting } from "@/lib/format";

import { CommandCenterMetrics } from "./components/metrics";
import { CopilotCard } from "./components/copilot-card";
import { GuestPulse } from "./components/guest-pulse";
import { NeedsAttention } from "./components/needs-attention";
import { OperationalOverview } from "./components/operational-overview";
import { TodaysOperations } from "./components/todays-operations";

function LoadingLayout() {
  return (
    <div aria-busy="true" aria-label="Loading command center">
      <Skeleton className="mb-5 h-[84px] w-full rounded-lg" />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-5">
          <div className="rounded-lg border bg-surface shadow-xs">
            <SkeletonRows rows={5} columns={3} />
          </div>
          <SkeletonBlock />
        </div>
        <div className="space-y-5">
          <SkeletonBlock />
          <SkeletonBlock />
        </div>
      </div>
    </div>
  );
}

export function CommandCenterView() {
  const { data, error, isPending, refetch, dataUpdatedAt } = useCommandCenter();
  const org = useCurrentOrganization();
  const me = useMe();
  const { status } = useRealtime();
  const canWrite = can(org.data, "reservations:write");
  const [creating, setCreating] = useState(false);
  const now = new Date();

  return (
    <div data-testid="command-center">
      <PageHeader
        eyebrow={new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long" }).format(now)}
        title={`${greeting(now)}, ${firstName(me.data?.full_name, me.data?.email)}`}
        description={
          <span className="inline-flex flex-wrap items-center gap-x-2">
            Here&apos;s what needs your attention today.
            {data ? (
              <span className="inline-flex items-center gap-1.5 text-xs text-subtle-foreground">
                {status === "live" ? <LiveDot /> : null}
                Updated {new Date(dataUpdatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
            ) : null}
          </span>
        }
        actions={
          canWrite ? (
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus /> New reservation
            </Button>
          ) : null
        }
      />

      {isPending ? (
        <LoadingLayout />
      ) : error ? (
        <div className="rounded-lg border bg-surface shadow-xs">
          <ErrorState error={error} onRetry={() => refetch()} />
        </div>
      ) : (
        <>
          <CommandCenterMetrics data={data} />
          {/* One flat grid: on small screens the order is operations → attention →
              activity → overview; on xl "Needs attention" becomes a sticky right rail. */}
          <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
            <div className="min-w-0 xl:col-start-1">
              <TodaysOperations data={data} canWrite={canWrite} />
            </div>
            <div className="min-w-0 space-y-5 xl:sticky xl:top-[68px] xl:col-start-2 xl:row-span-3 xl:row-start-1">
              <NeedsAttention signals={data.signals} />
              {can(org.data, "copilot:use") ? <CopilotCard /> : null}
              {can(org.data, "inbox:read") ? <GuestPulse metrics={data.metrics} /> : null}
            </div>
            <Panel
              className="xl:col-start-1"
              title="Recent activity"
              description="Every change across your operation, as it happens"
              icon={<History />}
              bodyClassName="max-h-[420px] overflow-y-auto scrollbar-thin"
              actions={
                <Link href="/activity" className="text-xs text-accent hover:underline">
                  View all
                </Link>
              }
            >
              {data.recent_activity.length === 0 ? (
                <EmptyState compact icon={History} title="No activity yet" description="Changes made by your team appear here in real time." />
              ) : (
                <ActivityFeed entries={data.recent_activity.slice(0, 12)} now={new Date(dataUpdatedAt)} />
              )}
            </Panel>
            <div className="min-w-0 xl:col-start-1">
              <OperationalOverview />
            </div>
          </div>
        </>
      )}
      <ReservationFormSheet open={creating} onOpenChange={setCreating} />
    </div>
  );
}
