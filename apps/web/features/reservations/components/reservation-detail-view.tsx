"use client";

import type { Reservation } from "@stayops/shared";
import { ArrowUpRight, Copy, Mail, MoreHorizontal, Pencil, Phone } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Avatar } from "@/components/ui/misc";
import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { ReservationTasks } from "@/features/tasks/components/reservation-tasks";
import { formatInZone, formatMoney, guestName, plural } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ReservationFormSheet } from "./reservation-form";
import { ReservationTimelinePanel } from "./reservation-timeline-panel";
import { StatusActions, useStatusTransition } from "./status-actions";
import { SourceLabel, StatusBadge } from "./status-badge";

function Section({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="border-b px-5 py-4 last:border-0">
      <div className="mb-2.5 flex items-center justify-between">
        <h3 className="eyebrow">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[112px_minmax(0,1fr)] gap-3 py-1 text-[13px]">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

function MoreActions({ r, canWrite, onEdit, fullPageLink }: { r: Reservation; canWrite: boolean; onEdit: () => void; fullPageLink: boolean }) {
  const { run, pending, dialog, targets } = useStatusTransition(r);
  return (
    <>
      <Menu>
        <MenuTrigger asChild>
          <Button size="icon-sm" aria-label="More actions">
            <MoreHorizontal />
          </Button>
        </MenuTrigger>
        <MenuContent className="w-52">
          {canWrite ? (
            <MenuItem icon={<Pencil />} onSelect={onEdit} disabled={r.status === "CHECKED_OUT" || r.status === "CANCELLED"}>
              Edit reservation
            </MenuItem>
          ) : null}
          <MenuItem
            icon={<Copy />}
            onSelect={() => {
              void navigator.clipboard?.writeText(r.confirmation_code);
              toast.success(`Copied ${r.confirmation_code}`);
            }}
          >
            Copy confirmation code
          </MenuItem>
          {fullPageLink ? (
            <MenuItem asChild>
              <Link href={`/reservations/${r.id}`}>
                <ArrowUpRight /> Open full page
              </Link>
            </MenuItem>
          ) : null}
          {canWrite && targets.includes("CANCELLED") ? (
            <>
              <MenuSeparator />
              <MenuItem tone="danger" disabled={pending} onSelect={() => run("CANCELLED")}>
                Cancel reservation
              </MenuItem>
            </>
          ) : null}
        </MenuContent>
      </Menu>
      {dialog}
    </>
  );
}

/** Reservation detail: used by the drawer (`?r=`) and the full page. */
export function ReservationDetailView({ r, canWrite, variant = "drawer" }: { r: Reservation; canWrite: boolean; variant?: "drawer" | "page" }) {
  const [editing, setEditing] = useState(false);
  const fresh = useRecentlyChanged(r.id);
  const tz = r.property.timezone;
  const fmt = (iso: string) => formatInZone(iso, tz, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  const time = (iso: string) => formatInZone(iso, tz, { hour: "2-digit", minute: "2-digit" });
  const perNight = r.nights > 0 ? Number(r.total_amount) / r.nights : Number(r.total_amount);
  const name = guestName(r.guest);
  const editable = canWrite && r.status !== "CHECKED_OUT" && r.status !== "CANCELLED";

  return (
    <div className={cn("flex min-h-full flex-col", variant === "page" && "overflow-hidden rounded-lg border bg-surface shadow-xs")}>
      <header className={cn("border-b px-5 pt-5 pb-4 transition-colors", fresh && "animate-row-in")}>
        <div className="flex items-start gap-3 pr-8">
          <Avatar name={name} size="lg" />
          <div className="min-w-0">
            <h2 className="truncate text-lg leading-tight font-semibold">{name}</h2>
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              Reservation <span className="font-mono text-foreground">#{r.confirmation_code}</span>
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <StatusBadge status={r.status} />
              <span className="text-xs text-muted-foreground">
                {r.property.name} · Unit {r.unit.name}
              </span>
            </div>
          </div>
        </div>
        {canWrite ? (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <StatusActions reservation={r} size="sm" includeCancel={false} />
            {editable ? (
              <Button size="sm" onClick={() => setEditing(true)}>
                <Pencil /> Edit
              </Button>
            ) : null}
            <MoreActions r={r} canWrite={canWrite} onEdit={() => setEditing(true)} fullPageLink={variant === "drawer"} />
          </div>
        ) : null}
      </header>

      <div className={cn(variant === "page" && "grid lg:grid-cols-[minmax(0,1fr)_400px]")}>
        <div className={cn(variant === "page" && "lg:border-r")}>
          <Section title="Stay">
            <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 rounded-md border bg-surface-raised px-3 py-2.5">
              <div>
                <div className="text-[11px] text-muted-foreground">Check-in</div>
                <div className="text-[13px] font-medium">{fmt(r.check_in)}</div>
                <div className="text-xs text-muted-foreground tabular">{time(r.check_in)}</div>
              </div>
              <div className="text-center text-[11px] text-muted-foreground">
                <div className="mx-auto mb-1 h-px w-10 bg-border-strong" aria-hidden />
                {plural(r.nights, "night")}
              </div>
              <div className="text-right">
                <div className="text-[11px] text-muted-foreground">Check-out</div>
                <div className="text-[13px] font-medium">{fmt(r.check_out)}</div>
                <div className="text-xs text-muted-foreground tabular">{time(r.check_out)}</div>
              </div>
            </div>
            <dl className="mt-2">
              <Row label="Guests">
                {plural(r.adults, "adult")}
                {r.children ? `, ${plural(r.children, "child", "children")}` : ""}
              </Row>
              <Row label="Source">
                <SourceLabel source={r.source} />
              </Row>
              <Row label="Timezone">
                <span className="text-muted-foreground">{tz}</span>
              </Row>
            </dl>
          </Section>

          <Section
            title="Guest"
            action={
              <Link href={`/guests/${r.guest.id}`} className="text-xs text-accent hover:underline">
                View profile
              </Link>
            }
          >
            <dl>
              <Row label="Name">{name}</Row>
              <Row label="Email">
                {r.guest.email ? (
                  <a className="inline-flex items-center gap-1.5 text-accent hover:underline" href={`mailto:${r.guest.email}`}>
                    <Mail className="size-3.5" /> {r.guest.email}
                  </a>
                ) : (
                  <span className="text-subtle-foreground">Not provided</span>
                )}
              </Row>
              <Row label="Phone">
                {r.guest.phone ? (
                  <a className="inline-flex items-center gap-1.5 tabular hover:underline" href={`tel:${r.guest.phone}`}>
                    <Phone className="size-3.5 text-muted-foreground" /> {r.guest.phone}
                  </a>
                ) : (
                  <span className="text-subtle-foreground">Not provided</span>
                )}
              </Row>
            </dl>
          </Section>

          <Section title="Property">
            <dl>
              <Row label="Property">
                <Link className="text-accent hover:underline" href={`/properties/${r.property.id}`}>
                  {r.property.name}
                </Link>
              </Row>
              <Row label="Unit">{r.unit.name}</Row>
            </dl>
          </Section>

          <Section title="Operations">
            <ReservationTasks reservationId={r.id} />
          </Section>

          <Section title="Payment summary">
            <dl>
              <Row label="Total">
                <span className="font-semibold tabular">{formatMoney(r.total_amount, r.currency)}</span>
              </Row>
              <Row label="Per night">
                <span className="tabular">{formatMoney(perNight, r.currency)}</span>
                <span className="text-muted-foreground"> · {plural(r.nights, "night")}</span>
              </Row>
              <Row label="Payments">
                <span className="text-subtle-foreground">Payment collection arrives with the Finance module</span>
              </Row>
            </dl>
          </Section>

          <Section title="Notes">
            {r.notes ? (
              <p className="text-[13px] leading-relaxed whitespace-pre-wrap">{r.notes}</p>
            ) : (
              <p className="text-[13px] text-subtle-foreground">No notes. {editable ? "Add internal notes from Edit." : ""}</p>
            )}
          </Section>
        </div>

        <Section title="Timeline">
          <ReservationTimelinePanel reservationId={r.id} tz={tz} />
        </Section>
      </div>
      <ReservationFormSheet open={editing} onOpenChange={setEditing} reservation={r} />
    </div>
  );
}
