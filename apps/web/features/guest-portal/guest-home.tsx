"use client";

import type { GuestStay } from "@stayops/shared";
import { BookOpen, CalendarDays, ClipboardList, KeyRound, MapPin, MessageCircle, Wifi, Wrench } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/states";
import { formatInZone } from "@/lib/format";

import { useGuestStay } from "./api";
import { directionsUrl, shortTime } from "./token";

const PHASE_COPY: Record<GuestStay["phase"], string> = {
  UPCOMING: "We're looking forward to hosting you.",
  ARRIVING_TODAY: "You arrive today — your check-in details are ready.",
  IN_HOUSE: "Enjoy your stay. We're a message away.",
  DEPARTING_TODAY: "Check-out is today. Safe travels!",
  DEPARTED: "Thanks for staying with us.",
};

function Action({ href, icon, label, external = false, testId }: { href: string; icon: ReactNode; label: string; external?: boolean; testId?: string }) {
  const className =
    "flex flex-col items-center justify-center gap-1.5 rounded-xl border bg-surface px-2 py-3.5 text-center text-xs font-medium shadow-xs active:bg-muted [&_svg]:size-5 [&_svg]:text-accent";
  return external ? (
    <a href={href} target="_blank" rel="noopener noreferrer" className={className} data-testid={testId}>
      {icon}
      {label}
    </a>
  ) : (
    <Link href={href} className={className} data-testid={testId}>
      {icon}
      {label}
    </Link>
  );
}

export function GuestHome() {
  const stay = useGuestStay();
  if (stay.isPending) {
    return (
      <div className="space-y-3" aria-busy="true">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-36 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (stay.error) return <p className="text-sm text-danger">{stay.error.message}</p>;

  const s = stay.data;
  const tz = s.property.timezone;
  const day = (iso: string) => formatInZone(iso, tz, { weekday: "short", day: "numeric", month: "short" });
  return (
    <div className="space-y-5" data-testid="guest-home">
      <section>
        <p className="text-sm text-muted-foreground">Welcome,</p>
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="guest-name">
          {s.guest_first_name}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">{PHASE_COPY[s.phase]}</p>
      </section>

      <section className="rounded-2xl border bg-surface p-4 shadow-xs" aria-label="Your stay" data-testid="guest-stay-card">
        <p className="text-[13px] font-semibold">{s.property.name}</p>
        <p className="text-xs text-muted-foreground">
          {s.unit_name} · {s.property.address}
        </p>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div className="rounded-lg bg-muted/60 p-3">
            <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Check-in</p>
            <p className="mt-0.5 text-[15px] font-semibold">{day(s.reservation.check_in)}</p>
            <p className="text-xs text-muted-foreground tabular">from {shortTime(s.property.check_in_time)}</p>
          </div>
          <div className="rounded-lg bg-muted/60 p-3">
            <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Check-out</p>
            <p className="mt-0.5 text-[15px] font-semibold">{day(s.reservation.check_out)}</p>
            <p className="text-xs text-muted-foreground tabular">by {shortTime(s.property.check_out_time)}</p>
          </div>
        </div>
        <p className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
          <CalendarDays className="size-3.5" />
          {s.reservation.nights} night{s.reservation.nights === 1 ? "" : "s"} · {s.reservation.adults} adult{s.reservation.adults === 1 ? "" : "s"}
          {s.reservation.children ? ` · ${s.reservation.children} child${s.reservation.children === 1 ? "" : "ren"}` : ""}
        </p>
        {!s.stay_info_available ? (
          <p className="mt-3 flex items-start gap-1.5 rounded-lg bg-accent-soft/60 px-3 py-2 text-xs">
            <KeyRound className="mt-0.5 size-3.5 shrink-0 text-accent" />
            Door codes and Wi-Fi appear here from {day(s.stay_info_available_from)}.
          </p>
        ) : null}
      </section>

      <section aria-label="Quick actions">
        <div className="grid grid-cols-3 gap-2.5">
          <Action href="/guest/stay#CHECK_IN" icon={<KeyRound />} label="Check-in info" testId="action-check-in" />
          <Action href="/guest/stay" icon={<BookOpen />} label="House manual" />
          <Action href="/guest/stay#WIFI" icon={<Wifi />} label="Wi-Fi" />
          <Action href={directionsUrl(s.property.address)} icon={<MapPin />} label="Directions" external />
          <Action href="/guest/messages" icon={<MessageCircle />} label="Contact host" testId="action-contact" />
          <Action href="/guest/requests?new=1" icon={<ClipboardList />} label="Request something" testId="action-request" />
        </div>
        <Link
          href="/guest/requests?new=1&type=MAINTENANCE"
          className="mt-2.5 flex items-center justify-center gap-2 rounded-xl border border-danger/20 bg-danger/5 py-3 text-[13px] font-medium text-danger"
          data-testid="action-report-issue"
        >
          <Wrench className="size-4" /> Report an issue
        </Link>
      </section>

      {s.open_requests || s.unread_messages ? (
        <section className="space-y-2">
          {s.unread_messages ? (
            <Link href="/guest/messages" className="block rounded-xl border bg-surface px-4 py-3 text-sm shadow-xs">
              <span className="font-medium">{s.unread_messages} new message{s.unread_messages === 1 ? "" : "s"}</span> from {s.host_name}
            </Link>
          ) : null}
          {s.open_requests ? (
            <Link href="/guest/requests" className="block rounded-xl border bg-surface px-4 py-3 text-sm shadow-xs">
              <span className="font-medium">{s.open_requests} open request{s.open_requests === 1 ? "" : "s"}</span> — we&apos;re on it
            </Link>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
