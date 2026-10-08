"use client";

import { ArrowLeft, BedDouble, History, Mail, MessageSquare, Phone, StickyNote, Heart, UserRound } from "lucide-react";
import Link from "next/link";

import { useBreadcrumb } from "@/components/shell/shell-context";
import { Badge } from "@/components/ui/badge";
import { Avatar, Metric, MetricStrip } from "@/components/ui/misc";
import { DescriptionItem, DescriptionList, Panel } from "@/components/ui/panel";
import { Tabs } from "@/components/ui/segmented";
import { ComingSoon, EmptyState, ErrorState, Skeleton, SkeletonRows } from "@/components/ui/states";
import { useAudit } from "@/features/activity/api";
import { ActivityTimeline } from "@/features/activity/components/activity-timeline";
import { useGuest } from "@/features/guests/api";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { useReservations } from "@/features/reservations/api";
import { ReservationDrawer } from "@/features/reservations/components/reservation-drawer";
import { ReservationTable } from "@/features/reservations/components/reservation-table";
import { ApiError } from "@/lib/api/client";
import { formatRelative, formatStayRange, guestName } from "@/lib/format";
import { useSearchState } from "@/lib/use-search-state";

type Tab = "reservations" | "activity" | "conversations" | "notes" | "preferences";

export function GuestDetail({ id }: { id: string }) {
  const guest = useGuest(id);
  const org = useCurrentOrganization();
  const { get, set } = useSearchState();
  const tab = (get("tab") || "reservations") as Tab;
  const reservations = useReservations({ guest_id: id, sort: "-check_in", limit: 100 }, { enabled: tab === "reservations" });
  const audit = useAudit({ entity_type: "guest", entity_id: id, limit: 50 }, { enabled: tab === "activity" });
  useBreadcrumb(guest.data ? guestName(guest.data) : null);

  if (guest.isPending) {
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Loading guest">
        <Skeleton className="h-16 w-80" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (guest.error) {
    if (guest.error instanceof ApiError && guest.error.status === 404) return <EmptyState icon={UserRound} title="Guest not found" />;
    return <ErrorState error={guest.error} onRetry={() => guest.refetch()} />;
  }
  const g = guest.data;
  const s = g.stats;
  const name = guestName(g);
  const now = new Date();

  return (
    <div>
      <Link href="/guests" className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Guests
      </Link>
      <div className="mb-5 flex items-start gap-4">
        <Avatar name={name} size="lg" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-[-0.01em]">{name}</h1>
            {s?.next_stay?.status === "CHECKED_IN" ? (
              <Badge tone="success" dot>
                In house
              </Badge>
            ) : null}
            {s && s.total_stays > 1 ? <Badge tone="accent">Returning guest</Badge> : null}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted-foreground">
            {g.email ? (
              <a href={`mailto:${g.email}`} className="inline-flex items-center gap-1.5 hover:text-foreground">
                <Mail className="size-3.5" /> {g.email}
              </a>
            ) : null}
            {g.phone ? (
              <a href={`tel:${g.phone}`} className="inline-flex items-center gap-1.5 tabular hover:text-foreground">
                <Phone className="size-3.5" /> {g.phone}
              </a>
            ) : null}
            <span className="text-xs text-subtle-foreground">Guest since {new Date(g.created_at).toLocaleDateString("en-GB", { month: "short", year: "numeric" })}</span>
          </div>
        </div>
      </div>

      <MetricStrip label="Guest summary" columns={4} className="mb-5">
        <Metric label="Total stays" value={s?.total_stays ?? 0} sub="Excluding cancellations" />
        <Metric label="Completed" value={s?.completed_stays ?? 0} sub="Checked out" />
        <Metric
          label={s?.next_stay?.status === "CHECKED_IN" ? "Current stay" : "Next stay"}
          value={s?.next_stay ? formatStayRange(s.next_stay.check_in, s.next_stay.check_out, s.next_stay.property_timezone) : "—"}
          sub={s?.next_stay ? `${s.next_stay.property_name} · Unit ${s.next_stay.unit_name}` : "Nothing booked"}
        />
        <Metric label="Last stay" value={s?.last_stay_at ? formatRelative(s.last_stay_at, now) : "—"} sub={s?.last_stay_at ? "Most recent check-out" : "No past stays"} />
      </MetricStrip>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <Tabs
            label="Guest sections"
            className="mb-4"
            value={tab}
            onChange={(t) => set({ tab: t === "reservations" ? null : t })}
            tabs={[
              { value: "reservations", label: "Reservations", count: reservations.data?.total },
              { value: "activity", label: "Activity" },
              { value: "conversations", label: "Conversations", badge: "Soon" },
              { value: "notes", label: "Notes", badge: "Soon" },
              { value: "preferences", label: "Preferences", badge: "Soon" },
            ]}
          />
          <div className="overflow-hidden rounded-lg border bg-surface shadow-xs">
            {tab === "reservations" ? (
              reservations.isPending ? (
                <SkeletonRows rows={4} columns={6} />
              ) : reservations.error ? (
                <ErrorState error={reservations.error} onRetry={() => reservations.refetch()} />
              ) : reservations.data.items.length === 0 ? (
                <EmptyState compact icon={BedDouble} title="No reservations yet" />
              ) : (
                <ReservationTable rows={reservations.data.items} canWrite={can(org.data, "reservations:write")} onOpen={(rid) => set({ r: rid }, { push: true })} />
              )
            ) : tab === "activity" ? (
              audit.isPending ? (
                <SkeletonRows rows={3} columns={2} />
              ) : audit.error ? (
                <ErrorState error={audit.error} onRetry={() => audit.refetch()} />
              ) : audit.data.items.length === 0 ? (
                <EmptyState compact icon={History} title="No activity yet" />
              ) : (
                <ActivityTimeline entries={audit.data.items} />
              )
            ) : tab === "conversations" ? (
              <ComingSoon icon={MessageSquare} title="Conversations" description="Messages across Airbnb, Booking.com, email and SMS will appear here with the Unified Inbox." />
            ) : tab === "notes" ? (
              <ComingSoon icon={StickyNote} title="Guest notes" description="Private team notes about this guest are planned for the CRM module." />
            ) : (
              <ComingSoon icon={Heart} title="Preferences" description="Stay preferences (early check-in, bedding, accessibility) will be captured here." />
            )}
          </div>
        </div>
        <Panel title="Contact">
          <DescriptionList>
            <DescriptionItem label="Name">{name}</DescriptionItem>
            <DescriptionItem label="Email">{g.email ?? <span className="text-subtle-foreground">Not provided</span>}</DescriptionItem>
            <DescriptionItem label="Phone">{g.phone ?? <span className="text-subtle-foreground">Not provided</span>}</DescriptionItem>
            <DescriptionItem label="Added">{formatRelative(g.created_at, now)}</DescriptionItem>
          </DescriptionList>
        </Panel>
      </div>
      <ReservationDrawer id={get("r") || null} onClose={() => set({ r: null })} />
    </div>
  );
}
