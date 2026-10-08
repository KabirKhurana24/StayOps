"use client";

import { GUEST_REQUEST_TYPES, type GuestRequestStatus, type GuestRequestType, zonedToUtcIso } from "@stayops/shared";
import { ClipboardList, Plus } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { ApiError, newIdempotencyKey } from "@/lib/api/client";
import { formatRelative } from "@/lib/format";

import { useCancelGuestRequest, useCreateGuestRequestAsGuest, useGuestRequests, useGuestStay } from "./api";

const GUEST_LABEL: Record<GuestRequestType, string> = {
  EARLY_CHECK_IN: "Early check-in",
  LATE_CHECKOUT: "Late checkout",
  EXTRA_TOWELS: "Extra towels",
  HOUSEKEEPING: "Housekeeping / cleaning",
  MAINTENANCE: "Something isn't working",
  QUESTION: "A question",
  OTHER: "Something else",
};
const STATUS: Record<GuestRequestStatus, { label: string; tone: "warning" | "info" | "accent" | "success" | "neutral" }> = {
  OPEN: { label: "Sent", tone: "warning" },
  ACKNOWLEDGED: { label: "Seen by host", tone: "info" },
  IN_PROGRESS: { label: "In progress", tone: "accent" },
  RESOLVED: { label: "Done", tone: "success" },
  CLOSED: { label: "Done", tone: "neutral" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
};

function NewRequestForm({ initialType, onDone }: { initialType: GuestRequestType; onDone: () => void }) {
  const create = useCreateGuestRequestAsGuest();
  const stay = useGuestStay();
  const [type, setType] = useState<GuestRequestType>(initialType);
  const [description, setDescription] = useState("");
  const [time, setTime] = useState("");
  // One key per form: a double tap or a retry on a flaky connection can't duplicate.
  const [idempotencyKey] = useState(() => newIdempotencyKey("guest-req"));
  const timed = type === "EARLY_CHECK_IN" || type === "LATE_CHECKOUT";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const s = stay.data;
    let requestedFor: string | undefined;
    if (timed && time && s) {
      const anchor = type === "EARLY_CHECK_IN" ? s.reservation.check_in : s.reservation.check_out;
      const date = new Intl.DateTimeFormat("en-CA", { timeZone: s.property.timezone }).format(new Date(anchor));
      requestedFor = zonedToUtcIso(date, time, s.property.timezone);
    }
    create.mutate({ type, description: description.trim(), requested_for: requestedFor, idempotencyKey }, { onSuccess: onDone });
  };

  return (
    <form onSubmit={submit} className="space-y-3 rounded-2xl border bg-surface p-4 shadow-xs" aria-label="New request">
      <Field label="What do you need?" htmlFor="guest-req-type">
        <Select id="guest-req-type" value={type} onChange={(e) => setType(e.target.value as GuestRequestType)}>
          {GUEST_REQUEST_TYPES.map((t) => (
            <option key={t} value={t}>
              {GUEST_LABEL[t]}
            </option>
          ))}
        </Select>
      </Field>
      {timed ? (
        <Field label={type === "EARLY_CHECK_IN" ? "Arrival time you'd like" : "Departure time you'd like"} htmlFor="guest-req-time" hint="Local time at the property">
          <Input id="guest-req-time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        </Field>
      ) : null}
      <Field label="Details" htmlFor="guest-req-details">
        <Textarea
          id="guest-req-details"
          rows={3}
          required
          maxLength={2000}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={type === "MAINTENANCE" ? "What's not working, and where?" : "Tell us a little more"}
        />
      </Field>
      {create.error ? <p className="text-xs text-danger">{create.error instanceof ApiError ? create.error.message : "Couldn't send. Try again."}</p> : null}
      <div className="flex justify-end gap-2">
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={create.isPending || !description.trim()}>
          {create.isPending ? "Sending…" : "Send request"}
        </Button>
      </div>
    </form>
  );
}

export function GuestRequests() {
  const params = useSearchParams();
  const router = useRouter();
  const requests = useGuestRequests();
  const cancel = useCancelGuestRequest();
  const creating = params.get("new") === "1";
  const typeParam = params.get("type");
  const initialType = (GUEST_REQUEST_TYPES as readonly string[]).includes(typeParam ?? "") ? (typeParam as GuestRequestType) : "HOUSEKEEPING";
  const close = () => router.replace("/guest/requests");

  return (
    <div className="space-y-4" data-testid="guest-requests">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Requests</h1>
        {!creating ? (
          <Button size="sm" variant="primary" onClick={() => router.replace("/guest/requests?new=1")}>
            <Plus /> New request
          </Button>
        ) : null}
      </div>
      {creating ? <NewRequestForm key={initialType} initialType={initialType} onDone={close} /> : null}
      {requests.isPending ? (
        <Skeleton className="h-20 w-full" />
      ) : requests.data?.length ? (
        <ul className="space-y-2.5">
          {requests.data.map((r) => (
            <li key={r.id} className="rounded-2xl border bg-surface p-4 shadow-xs" data-testid="guest-request-item" data-status={r.status}>
              <div className="flex items-start justify-between gap-2">
                <p className="text-[14px] font-medium">{r.label}</p>
                <Badge tone={STATUS[r.status].tone} dot>
                  {STATUS[r.status].label}
                </Badge>
              </div>
              <p className="mt-1 text-[13px] text-muted-foreground">{r.description}</p>
              <div className="mt-2 flex items-center justify-between text-xs text-subtle-foreground">
                <span>{formatRelative(r.created_at)}</span>
                {r.can_cancel ? (
                  <button type="button" className="font-medium text-muted-foreground underline-offset-2 hover:underline" onClick={() => cancel.mutate(r.id)} disabled={cancel.isPending}>
                    Cancel request
                  </button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : !creating ? (
        <EmptyState icon={ClipboardList} title="No requests yet" description="Need towels, a late checkout or something fixed? Ask here and we'll take care of it." />
      ) : null}
    </div>
  );
}
