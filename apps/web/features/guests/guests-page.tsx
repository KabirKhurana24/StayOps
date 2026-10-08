"use client";

import { type Guest, guestInputSchema } from "@stayops/shared";
import { Plus, Users } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useDeferredValue, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FormError, Input, SearchInput } from "@/components/ui/form";
import { Avatar } from "@/components/ui/misc";
import { PageHeader } from "@/components/ui/panel";
import { Sheet } from "@/components/ui/sheet";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Table, TableScroll, Td, Th, THead, Tr } from "@/components/ui/table";
import { useCreateGuest, useGuests } from "@/features/guests/api";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { ApiError } from "@/lib/api/client";
import { formatRelative, formatStayRange, guestName, plural } from "@/lib/format";

function GuestForm({ onDone }: { onDone: () => void }) {
  const create = useCreateGuest();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const parsed = guestInputSchema.safeParse(Object.fromEntries(new FormData(event.currentTarget).entries()));
    if (!parsed.success) {
      setErrors(Object.fromEntries(parsed.error.issues.map((i) => [String(i.path[0]), i.message])));
      return;
    }
    setErrors({});
    const { email, phone, ...rest } = parsed.data;
    create.mutate(
      { ...rest, email: email || undefined, phone: phone || undefined },
      {
        onSuccess: (g) => {
          toast.success(`${guestName(g)} added`);
          onDone();
        },
      },
    );
  };
  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <div className="grid grid-cols-2 gap-3">
        <Field label="First name" htmlFor="first_name" error={errors.first_name}>
          <Input id="first_name" name="first_name" autoFocus />
        </Field>
        <Field label="Last name" htmlFor="last_name" error={errors.last_name}>
          <Input id="last_name" name="last_name" />
        </Field>
      </div>
      <Field label="Email" htmlFor="email" error={errors.email} hint="Returning guests are matched by email when booking.">
        <Input id="email" name="email" type="email" />
      </Field>
      <Field label="Phone" htmlFor="phone" error={errors.phone}>
        <Input id="phone" name="phone" type="tel" placeholder="+44 7700 900123" />
      </Field>
      <FormError message={create.error instanceof ApiError ? create.error.message : null} />
      <div className="flex justify-end gap-2 pt-1">
        <Button onClick={onDone}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={create.isPending}>
          {create.isPending ? "Saving…" : "Add guest"}
        </Button>
      </div>
    </form>
  );
}

function UpcomingStay({ g }: { g: Guest }) {
  const next = g.stats?.next_stay;
  if (!next) return <span className="text-subtle-foreground">—</span>;
  const inHouse = next.status === "CHECKED_IN";
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2">
        <span className="whitespace-nowrap">{formatStayRange(next.check_in, next.check_out, next.property_timezone)}</span>
        {inHouse ? (
          <Badge tone="success" dot>
            In house
          </Badge>
        ) : null}
      </div>
      <div className="truncate text-xs text-muted-foreground">
        {next.property_name} · Unit {next.unit_name}
      </div>
    </div>
  );
}

export function GuestsPage() {
  const [q, setQ] = useState("");
  const deferred = useDeferredValue(q.trim());
  const guests = useGuests(deferred, 100);
  const org = useCurrentOrganization();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const now = new Date();

  return (
    <div>
      <PageHeader
        title="Guests"
        description="Everyone who has stayed with you, and who's arriving next."
        actions={
          can(org.data, "guests:write") ? (
            <Button variant="primary" onClick={() => setOpen(true)}>
              <Plus /> Add guest
            </Button>
          ) : null
        }
      />
      <div className="overflow-hidden rounded-lg border bg-surface shadow-xs">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
          <SearchInput
            wrapperClassName="w-full sm:w-72"
            placeholder="Search name or email…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onClear={() => setQ("")}
            aria-label="Search guests"
          />
          {guests.data ? <span className="text-xs text-muted-foreground tabular">{plural(guests.data.total, "guest")}</span> : null}
        </div>
        {guests.isPending ? (
          <SkeletonRows rows={8} columns={5} />
        ) : guests.error ? (
          <ErrorState error={guests.error} onRetry={() => guests.refetch()} />
        ) : guests.data.items.length === 0 ? (
          <EmptyState
            icon={Users}
            title={q ? "No guests match your search" : "No guests yet"}
            description={q ? "Try a different name or email." : "Guests are created automatically when you add reservations."}
          />
        ) : (
          <>
            <TableScroll className="hidden md:block">
              <Table className="min-w-[820px]">
                <THead>
                  <tr>
                    <Th>Guest</Th>
                    <Th>Upcoming stay</Th>
                    <Th>Total stays</Th>
                    <Th>Contact</Th>
                    <Th>Latest activity</Th>
                  </tr>
                </THead>
                <tbody>
                  {guests.data.items.map((g) => {
                    const last = g.stats?.last_stay_at;
                    return (
                      <Tr
                        key={g.id}
                        interactive
                        tabIndex={0}
                        onClick={() => router.push(`/guests/${g.id}`)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") router.push(`/guests/${g.id}`);
                        }}
                        data-testid="guest-row"
                      >
                        <Td>
                          <div className="flex items-center gap-2.5">
                            <Avatar name={guestName(g)} size="sm" />
                            <div className="min-w-0">
                              <Link href={`/guests/${g.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
                                {guestName(g)}
                              </Link>
                              <div className="truncate text-xs text-muted-foreground">{g.email ?? "No email"}</div>
                            </div>
                          </div>
                        </Td>
                        <Td className="max-w-[260px]">
                          <UpcomingStay g={g} />
                        </Td>
                        <Td>
                          <span className="font-medium tabular">{g.stats?.total_stays ?? 0}</span>
                          {g.stats && g.stats.total_stays > 1 ? (
                            <Badge tone="accent" className="ml-2">
                              Returning
                            </Badge>
                          ) : null}
                        </Td>
                        <Td className="text-muted-foreground tabular">{g.phone ?? "—"}</Td>
                        <Td className="text-xs text-muted-foreground">
                          {g.stats?.next_stay?.status === "CHECKED_IN"
                            ? "In house now"
                            : last
                              ? `Last stay ${formatRelative(last, now)}`
                              : `Added ${formatRelative(g.created_at, now)}`}
                        </Td>
                      </Tr>
                    );
                  })}
                </tbody>
              </Table>
            </TableScroll>
            <ul className="divide-y md:hidden">
              {guests.data.items.map((g) => (
                <li key={g.id}>
                  <Link href={`/guests/${g.id}`} className="flex gap-3 px-4 py-3 hover:bg-muted/45">
                    <Avatar name={guestName(g)} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-medium">{guestName(g)}</div>
                      <div className="truncate text-xs text-muted-foreground">
                        {g.stats?.next_stay ? `${g.stats.next_stay.property_name} · ${formatStayRange(g.stats.next_stay.check_in, g.stats.next_stay.check_out, g.stats.next_stay.property_timezone)}` : plural(g.stats?.total_stays ?? 0, "stay")}
                      </div>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
      <Sheet open={open} onOpenChange={setOpen} title="Add guest">
        <GuestForm onDone={() => setOpen(false)} />
      </Sheet>
    </div>
  );
}
