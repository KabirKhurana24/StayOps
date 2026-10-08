"use client";

import {
  addDays,
  daysBetween,
  RESERVATION_SOURCES,
  type Reservation,
  type ReservationSource,
  todayIn,
  type UnitAvailability,
  utcToZoned,
  zonedToUtcIso,
} from "@stayops/shared";
import { CircleCheck, CircleX, Loader2, Wrench } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, useDeferredValue, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Field, FormError, Input, SearchInput, Select, Textarea } from "@/components/ui/form";
import { Sheet } from "@/components/ui/sheet";
import { useGuests } from "@/features/guests/api";
import { useCurrentOrganization } from "@/features/organizations/api";
import { useProperties, useUnits } from "@/features/properties/api";
import { useAvailability, useCreateReservation, useUpdateReservation } from "@/features/reservations/api";
import { ApiError, newIdempotencyKey } from "@/lib/api/client";
import { formatInZone, formatMoney, guestName, plural, SOURCE_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIME = /^\d{2}:\d{2}$/;

const detailsSchema = z.object({
  adults: z.coerce.number().int().min(1, "At least 1 adult").max(50),
  children: z.coerce.number().int().min(0).max(50),
  source: z.enum(RESERVATION_SOURCES),
  total_amount: z.string().regex(/^\d{1,10}(\.\d{1,2})?$/, "Enter an amount like 450 or 450.00"),
  currency: z.string().regex(/^[A-Z]{3}$/, "3-letter code"),
  notes: z.string().max(2000).optional(),
});

type FieldErrors = Partial<Record<string, string>>;

/** Calendar selections prefill a new reservation (local dates, property timezone). */
export type ReservationPrefill = { propertyId: string; unitId: string; checkIn: string; checkOut: string };

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="space-y-3 border-b pb-5 last:border-0">
      <div className="flex items-center justify-between">
        <h3 className="eyebrow">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function conflictRange(c: { check_in: string; check_out: string }, tz: string) {
  const f = (iso: string) => formatInZone(iso, tz, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  return `${f(c.check_in)} → ${f(c.check_out)}`;
}

/** Every unit of the property for the chosen dates; click to pick one. */
function UnitAvailabilityList({
  units,
  selected,
  onSelect,
  tz,
  loading,
}: {
  units: UnitAvailability[];
  selected: string;
  onSelect: (id: string) => void;
  tz: string;
  loading: boolean;
}) {
  return (
    <div className={cn("overflow-hidden rounded-md border", loading && "opacity-60")} role="list" aria-label="Unit availability" data-testid="unit-availability">
      {units.map((u) => {
        const conflict = u.conflicts[0];
        const Icon = !u.bookable ? Wrench : u.available ? CircleCheck : CircleX;
        return (
          <div key={u.unit_id} role="listitem" className="border-b last:border-0">
          <button
            type="button"
            onClick={() => onSelect(u.unit_id)}
            aria-pressed={selected === u.unit_id}
            data-available={u.available}
            className={cn(
              "flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] transition-colors hover:bg-muted/50",
              selected === u.unit_id && "bg-accent-soft/60 hover:bg-accent-soft/70",
            )}
          >
            <Icon className={cn("size-3.5 shrink-0", u.available ? "text-success" : !u.bookable ? "text-warning" : "text-danger")} aria-hidden />
            <span className="w-14 shrink-0 font-medium">{u.name}</span>
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
              {u.available
                ? "Available"
                : !u.bookable
                  ? u.reason
                  : `Booked · ${conflict?.guest_name} (${conflict?.confirmation_code}) · ${conflict ? conflictRange(conflict, tz) : ""}`}
            </span>
            {selected === u.unit_id ? <span className="text-[11px] font-medium text-accent">Selected</span> : null}
          </button>
          </div>
        );
      })}
    </div>
  );
}

function GuestPicker({
  mode,
  setMode,
  guestId,
  setGuestId,
  errors,
}: {
  mode: "existing" | "new";
  setMode: (m: "existing" | "new") => void;
  guestId: string;
  setGuestId: (id: string) => void;
  errors: FieldErrors;
}) {
  const [query, setQuery] = useState("");
  const guests = useGuests(useDeferredValue(query), 20, { stats: false });
  return (
    <Section
      title="Guest"
      aside={
        <Button type="button" variant="link" size="sm" onClick={() => setMode(mode === "existing" ? "new" : "existing")}>
          {mode === "existing" ? "+ New guest" : "Pick existing"}
        </Button>
      }
    >
      {mode === "existing" ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <SearchInput placeholder="Search name or email…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search guests" />
          <Select name="guest_id" aria-label="Guest" value={guestId} onChange={(e) => setGuestId(e.target.value)} aria-invalid={!!errors.guest}>
            <option value="">Select guest…</option>
            {guests.data?.items.map((g) => (
              <option key={g.id} value={g.id}>
                {guestName(g)}
                {g.email ? ` — ${g.email}` : ""}
              </option>
            ))}
          </Select>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <Field label="First name" htmlFor="guest_first_name" error={errors.guest_first_name}>
            <Input id="guest_first_name" name="guest_first_name" autoComplete="off" />
          </Field>
          <Field label="Last name" htmlFor="guest_last_name" error={errors.guest_last_name}>
            <Input id="guest_last_name" name="guest_last_name" autoComplete="off" />
          </Field>
          <Field label="Email" htmlFor="guest_email" error={errors.guest_email} hint="Matches a returning guest if the email exists.">
            <Input id="guest_email" name="guest_email" type="email" autoComplete="off" />
          </Field>
          <Field label="Phone" htmlFor="guest_phone">
            <Input id="guest_phone" name="guest_phone" type="tel" autoComplete="off" />
          </Field>
        </div>
      )}
      {errors.guest ? (
        <p className="text-xs text-danger" role="alert">
          {errors.guest}
        </p>
      ) : null}
    </Section>
  );
}

/** Create (no `reservation`) or edit an existing reservation. */
export function ReservationFormSheet({
  open,
  onOpenChange,
  reservation,
  prefill,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  reservation?: Reservation;
  prefill?: ReservationPrefill;
}) {
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={reservation ? `Edit ${reservation.confirmation_code}` : "New reservation"}
      description={reservation ? "Changes are validated, audited and pushed live to every operator." : "Dates and times are in the property's local timezone."}
      bodyClassName="p-0"
    >
      {open ? <ReservationForm reservation={reservation} prefill={prefill} onDone={() => onOpenChange(false)} /> : null}
    </Sheet>
  );
}

function ReservationForm({ reservation, prefill, onDone }: { reservation?: Reservation; prefill?: ReservationPrefill; onDone: () => void }) {
  const router = useRouter();
  const properties = useProperties();
  const org = useCurrentOrganization();
  const create = useCreateReservation();
  const update = useUpdateReservation(reservation?.id ?? "", { inlineErrors: true });
  // One key per form session: double-submits and retries can't double-book.
  const [idempotencyKey] = useState(() => newIdempotencyKey("reservation"));

  const initialTz = reservation?.property.timezone;
  const [propertyId, setPropertyId] = useState(reservation?.property.id ?? prefill?.propertyId ?? "");
  const property = properties.data?.items.find((p) => p.id === propertyId);
  const tz = property?.timezone ?? initialTz ?? "UTC";
  const units = useUnits(propertyId || undefined);

  const initial = (() => {
    if (reservation && initialTz) {
      const ci = utcToZoned(reservation.check_in, initialTz);
      const co = utcToZoned(reservation.check_out, initialTz);
      return { ciDate: ci.date, ciTime: ci.time, coDate: co.date, coTime: co.time };
    }
    const today = todayIn(tz);
    return {
      ciDate: prefill?.checkIn ?? today,
      ciTime: property?.default_check_in_time.slice(0, 5) ?? "15:00",
      coDate: prefill?.checkOut ?? addDays(prefill?.checkIn ?? today, 2),
      coTime: property?.default_check_out_time.slice(0, 5) ?? "11:00",
    };
  })();

  const [unitId, setUnitId] = useState(reservation?.unit.id ?? prefill?.unitId ?? "");
  const [ciDate, setCiDate] = useState(initial.ciDate);
  const [ciTime, setCiTime] = useState(initial.ciTime);
  const [coDate, setCoDate] = useState(initial.coDate);
  const [coTime, setCoTime] = useState(initial.coTime);
  const [guestMode, setGuestMode] = useState<"existing" | "new">("existing");
  const [guestId, setGuestId] = useState(reservation?.guest.id ?? "");
  const [amount, setAmount] = useState(reservation?.total_amount ?? "");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [serverError, setServerError] = useState<ApiError | null>(null);

  const changeProperty = (id: string) => {
    setPropertyId(id);
    setUnitId("");
    const p = properties.data?.items.find((x) => x.id === id);
    if (p && !reservation) {
      setCiTime(p.default_check_in_time.slice(0, 5));
      setCoTime(p.default_check_out_time.slice(0, 5));
    }
  };

  const datesValid = DATE.test(ciDate) && DATE.test(coDate) && TIME.test(ciTime) && TIME.test(coTime) && `${coDate}T${coTime}` > `${ciDate}T${ciTime}`;
  const nights = datesValid ? daysBetween(ciDate, coDate) : 0;
  // Defer a primitive key (not a fresh object) so typing doesn't re-query on every keystroke.
  const availabilityKey = useDeferredValue(propertyId && datesValid ? [propertyId, ciDate, ciTime, coDate, coTime, tz].join("|") : "");
  const availabilityParams = (() => {
    if (!availabilityKey) return null;
    const [pid = "", ciD = "", ciT = "", coD = "", coT = "", zone = "UTC"] = availabilityKey.split("|");
    return {
      property_id: pid,
      check_in: zonedToUtcIso(ciD, ciT, zone),
      check_out: zonedToUtcIso(coD, coT, zone),
      ...(reservation ? { exclude_reservation_id: reservation.id } : {}),
    };
  })();
  const availability = useAvailability(availabilityParams);
  const selected = availability.data?.units.find((u) => u.unit_id === unitId);
  const blocked = Boolean(selected && !selected.available);
  const unitName = selected?.name ?? units.data?.find((u) => u.id === unitId)?.name;
  const pending = create.isPending || update.isPending;
  const currencyDefault = reservation?.currency ?? org.data?.organization.default_currency ?? "EUR";

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setServerError(null);
    const form = new FormData(event.currentTarget);
    const get = (k: string) => (form.get(k) as string | null) ?? "";
    const next: FieldErrors = {};
    if (!unitId) next.unit_id = "Choose a unit";
    if (!DATE.test(ciDate)) next.check_in_date = "Choose a date";
    if (!DATE.test(coDate)) next.check_out_date = "Choose a date";
    else if (!datesValid) next.check_out_date = "Check-out must be after check-in";

    const parsed = detailsSchema.safeParse({
      adults: get("adults"),
      children: get("children"),
      source: get("source"),
      total_amount: amount.trim(),
      currency: get("currency").trim().toUpperCase(),
      notes: get("notes") || undefined,
    });
    if (!parsed.success) for (const i of parsed.error.issues) next[String(i.path[0])] ??= i.message;

    let guest: { first_name: string; last_name: string; email?: string; phone?: string } | undefined;
    if (!reservation) {
      if (guestMode === "existing" && !guestId) next.guest = "Select a guest or add a new one";
      if (guestMode === "new") {
        guest = {
          first_name: get("guest_first_name").trim(),
          last_name: get("guest_last_name").trim(),
          email: get("guest_email").trim() || undefined,
          phone: get("guest_phone").trim() || undefined,
        };
        if (!guest.first_name) next.guest_first_name = "Required";
        if (!guest.last_name) next.guest_last_name = "Required";
        if (guest.email && !z.email().safeParse(guest.email).success) next.guest_email = "Invalid email";
      }
    }
    setErrors(next);
    if (!parsed.success || Object.keys(next).length || blocked) return;

    const v = parsed.data;
    const check_in = zonedToUtcIso(ciDate, ciTime, tz);
    const check_out = zonedToUtcIso(coDate, coTime, tz);
    try {
      if (reservation) {
        const updated = await update.mutateAsync({
          unit_id: unitId,
          check_in,
          check_out,
          adults: v.adults,
          children: v.children,
          source: v.source,
          total_amount: v.total_amount,
          currency: v.currency,
          notes: v.notes ?? null,
          expected_version: reservation.version,
        });
        toast.success(`Saved ${updated.confirmation_code}`, { description: `${guestName(updated.guest)} · Unit ${updated.unit.name}` });
      } else {
        const created = await create.mutateAsync({
          idempotencyKey,
          body: {
            unit_id: unitId,
            ...(guestMode === "existing" ? { guest_id: guestId } : { guest }),
            check_in,
            check_out,
            status: (get("status") as "PENDING" | "CONFIRMED") || "CONFIRMED",
            adults: v.adults,
            children: v.children,
            source: v.source,
            total_amount: v.total_amount,
            currency: v.currency,
            notes: v.notes,
          },
        });
        toast.success(`Reservation ${created.confirmation_code} created`, {
          description: `${guestName(created.guest)} · ${created.property.name}, unit ${created.unit.name}`,
          action: { label: "Open", onClick: () => router.push(`/reservations?r=${created.id}`) },
        });
      }
      onDone();
    } catch (error) {
      // Server rules (overlap races, stale versions) are surfaced inline.
      setServerError(error instanceof ApiError ? error : new ApiError(0, "unknown", "Could not save the reservation."));
      void availability.refetch();
    }
  }

  const serverConflict = serverError?.details as { conflicting_reservation_id?: string } | undefined;
  const perNight = nights > 0 && /^\d+(\.\d{1,2})?$/.test(amount) ? Number(amount) / nights : null;

  return (
    <form onSubmit={onSubmit} className="flex min-h-full flex-col" noValidate aria-label="Reservation form">
      <div className="flex-1 space-y-5 px-5 py-5">
        <Section title="Stay">
          <Field label="Property" htmlFor="property_id">
            <Select id="property_id" value={propertyId} onChange={(e) => changeProperty(e.target.value)} disabled={properties.isPending}>
              <option value="">Select property…</option>
              {properties.data?.items
                .filter((p) => p.status === "ACTIVE" || p.id === reservation?.property.id)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
            </Select>
          </Field>
          <div className="grid grid-cols-[minmax(0,1fr)_120px] gap-3 sm:grid-cols-[minmax(0,1fr)_120px_minmax(0,1fr)_120px]">
            <Field label="Check-in" htmlFor="check_in_date" error={errors.check_in_date}>
              <Input id="check_in_date" name="check_in_date" type="date" value={ciDate} onChange={(e) => setCiDate(e.target.value)} />
            </Field>
            <Field label="Time" htmlFor="check_in_time">
              <Input id="check_in_time" name="check_in_time" type="time" value={ciTime} onChange={(e) => setCiTime(e.target.value)} />
            </Field>
            <Field label="Check-out" htmlFor="check_out_date" error={errors.check_out_date}>
              <Input id="check_out_date" name="check_out_date" type="date" value={coDate} min={ciDate || undefined} onChange={(e) => setCoDate(e.target.value)} />
            </Field>
            <Field label="Time" htmlFor="check_out_time">
              <Input id="check_out_time" name="check_out_time" type="time" value={coTime} onChange={(e) => setCoTime(e.target.value)} />
            </Field>
          </div>
          <p className="-mt-1 text-[11px] text-subtle-foreground">
            {nights > 0 ? `${plural(nights, "night")} · ` : ""}local time in {tz.replaceAll("_", " ")}
          </p>
          <Field label="Unit" htmlFor="unit_id" error={errors.unit_id}>
            <Select id="unit_id" name="unit_id" value={unitId} onChange={(e) => setUnitId(e.target.value)} disabled={!propertyId || units.isPending} aria-invalid={!!errors.unit_id || blocked}>
              <option value="">{propertyId ? "Select unit…" : "Pick a property first"}</option>
              {units.data
                ?.filter((u) => u.status === "ACTIVE" || u.id === reservation?.unit.id)
                .map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                  </option>
                ))}
            </Select>
          </Field>
          {propertyId && datesValid ? (
            availability.isPending ? (
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" aria-hidden /> Checking availability…
              </p>
            ) : availability.data ? (
              <UnitAvailabilityList units={availability.data.units} selected={unitId} onSelect={setUnitId} tz={tz} loading={availability.isFetching} />
            ) : null
          ) : null}
          {blocked && selected ? (
            <div role="alert" className="rounded-md border border-danger/25 bg-danger/5 px-3 py-2.5 text-[13px]" data-testid="availability-conflict">
              <p className="font-medium text-danger">
                {property?.name ? `${unitName ? `Unit ${unitName}` : "This unit"} at ${property.name}` : `Unit ${unitName}`} is unavailable for these dates.
              </p>
              {selected.conflicts[0] ? (
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Conflicts with {selected.conflicts[0].guest_name} ·{" "}
                  <Link href={`/reservations?r=${selected.conflicts[0].reservation_id}`} className="font-mono text-foreground underline-offset-2 hover:underline">
                    {selected.conflicts[0].confirmation_code}
                  </Link>{" "}
                  · {conflictRange(selected.conflicts[0], tz)}
                </p>
              ) : selected.reason ? (
                <p className="mt-0.5 text-xs text-muted-foreground">{selected.reason}</p>
              ) : null}
            </div>
          ) : null}
        </Section>

        {!reservation ? (
          <GuestPicker mode={guestMode} setMode={setGuestMode} guestId={guestId} setGuestId={setGuestId} errors={errors} />
        ) : (
          <Section title="Guest">
            <p className="text-[13px]">
              {guestName(reservation.guest)} <span className="text-muted-foreground">· {reservation.guest.email ?? "no email"}</span>
            </p>
          </Section>
        )}

        <Section title="Booking">
          <div className="grid grid-cols-3 gap-3">
            <Field label="Adults" htmlFor="adults" error={errors.adults}>
              <Input id="adults" name="adults" type="number" min={1} max={50} defaultValue={reservation?.adults ?? 2} />
            </Field>
            <Field label="Children" htmlFor="children" error={errors.children}>
              <Input id="children" name="children" type="number" min={0} max={50} defaultValue={reservation?.children ?? 0} />
            </Field>
            <Field label="Source" htmlFor="source">
              <Select id="source" name="source" defaultValue={reservation?.source ?? ("DIRECT" satisfies ReservationSource)}>
                {RESERVATION_SOURCES.map((s) => (
                  <option key={s} value={s}>
                    {SOURCE_LABEL[s]}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          {!reservation ? (
            <Field label="Status" htmlFor="status" hint="Pending bookings still hold the unit until confirmed or cancelled.">
              <Select id="status" name="status" defaultValue="CONFIRMED">
                <option value="CONFIRMED">Confirmed</option>
                <option value="PENDING">Pending</option>
              </Select>
            </Field>
          ) : null}
        </Section>

        <Section title="Payment">
          <div className="grid grid-cols-3 gap-3">
            <Field
              label="Total amount"
              htmlFor="total_amount"
              error={errors.total_amount}
              className="col-span-2"
              hint={perNight !== null ? `${formatMoney(perNight, currencyDefault)} per night` : undefined}
            >
              <Input id="total_amount" name="total_amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.00" className="tabular" />
            </Field>
            <Field label="Currency" htmlFor="currency" error={errors.currency}>
              <Input id="currency" name="currency" maxLength={3} defaultValue={currencyDefault} className="uppercase" />
            </Field>
          </div>
        </Section>

        <Section title="Notes">
          <Field label="Internal notes" htmlFor="notes">
            <Textarea id="notes" name="notes" defaultValue={reservation?.notes ?? ""} maxLength={2000} placeholder="Arrival details, special requests…" />
          </Field>
        </Section>

        {serverError ? (
          <FormError message={serverError.message}>
            {serverConflict?.conflicting_reservation_id ? (
              <Link href={`/reservations?r=${serverConflict.conflicting_reservation_id}`} className="mt-1 inline-block text-xs underline">
                View conflicting reservation
              </Link>
            ) : null}
          </FormError>
        ) : null}
      </div>

      <div className="sticky bottom-0 flex items-center justify-between gap-3 border-t bg-surface-raised/95 px-5 py-3 backdrop-blur">
        <p className="min-w-0 truncate text-xs text-muted-foreground">
          {unitName && nights > 0 ? `Unit ${unitName} · ${plural(nights, "night")}` : "Choose a unit and dates"}
          {blocked ? <span className="text-danger"> · unavailable</span> : null}
        </p>
        <div className="flex shrink-0 gap-2">
          <Button onClick={onDone}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={pending || blocked}>
            {pending ? "Saving…" : reservation ? "Save changes" : "Create reservation"}
          </Button>
        </div>
      </div>
    </form>
  );
}
