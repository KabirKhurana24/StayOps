"use client";

import { GUEST_REQUEST_LABEL, type Guest, type GuestPreferences } from "@stayops/shared";
import { ClipboardList, Lock, MessageSquare } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/form";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { useGuestRequests } from "@/features/guest-requests/api";
import { useConversations } from "@/features/inbox/api";
import { previewLine, REQUEST_STATUS_LABEL, REQUEST_STATUS_TONE } from "@/features/inbox/model";
import { ApiError } from "@/lib/api/client";
import { formatRelative, formatStayRange } from "@/lib/format";

import { useUpdateGuest } from "../api";

export function GuestConversations({ guestId }: { guestId: string }) {
  const convs = useConversations({ guest_id: guestId, limit: 50 });
  if (convs.isPending) return <SkeletonRows rows={3} columns={2} />;
  if (convs.error) return <ErrorState error={convs.error} onRetry={() => convs.refetch()} />;
  if (!convs.data.items.length) return <EmptyState compact icon={MessageSquare} title="No conversations yet" description="Start one from a reservation, or share the guest portal link." />;
  return (
    <ul className="divide-y" data-testid="guest-conversations">
      {convs.data.items.map((c) => (
        <li key={c.id}>
          <Link href={`/inbox?c=${c.id}`} className="flex items-start justify-between gap-3 px-4 py-3 hover:bg-muted/50">
            <span className="min-w-0">
              <span className="block text-[13px] font-medium">
                {c.property?.name ?? "No stay"}
                {c.unit ? ` · ${c.unit.name}` : ""}
                {c.reservation && c.property ? ` · ${formatStayRange(c.reservation.check_in, c.reservation.check_out, c.property.timezone)}` : ""}
              </span>
              <span className="mt-0.5 block truncate text-xs text-muted-foreground">{previewLine(c)}</span>
            </span>
            <span className="shrink-0 text-right text-xs text-subtle-foreground">
              {c.last_message_at ? formatRelative(c.last_message_at) : ""}
              <span className="mt-0.5 block">{c.status.toLowerCase()}</span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function GuestRequestsTab({ guestId }: { guestId: string }) {
  const reqs = useGuestRequests({ guest_id: guestId });
  if (reqs.isPending) return <SkeletonRows rows={3} columns={2} />;
  if (reqs.error) return <ErrorState error={reqs.error} onRetry={() => reqs.refetch()} />;
  if (!reqs.data.items.length) return <EmptyState compact icon={ClipboardList} title="No requests yet" />;
  return (
    <ul className="divide-y" data-testid="guest-requests-tab">
      {reqs.data.items.map((r) => (
        <li key={r.id} className="flex items-start justify-between gap-3 px-4 py-3">
          <Link href={`/inbox?c=${r.conversation_id}`} className="min-w-0 hover:underline">
            <span className="block text-[13px] font-medium">{GUEST_REQUEST_LABEL[r.type]}</span>
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              {r.confirmation_code} · Unit {r.unit.name} · {r.description}
            </span>
          </Link>
          <span className="flex shrink-0 flex-col items-end gap-1 text-xs text-subtle-foreground">
            <Badge tone={REQUEST_STATUS_TONE[r.status]} dot>
              {REQUEST_STATUS_LABEL[r.status]}
            </Badge>
            {formatRelative(r.created_at)}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function GuestNotes({ guest, canWrite }: { guest: Guest; canWrite: boolean }) {
  const update = useUpdateGuest(guest.id);
  const [notes, setNotes] = useState(guest.notes ?? "");
  const dirty = notes !== (guest.notes ?? "");
  const save = (e: FormEvent) => {
    e.preventDefault();
    update.mutate({ notes: notes.trim() || null, expected_version: guest.version }, { onSuccess: () => toast.success("Notes saved") });
  };
  return (
    <form onSubmit={save} className="space-y-3 p-4" aria-label="Guest notes">
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Lock className="size-3" /> Private to your team — never shown to the guest or in the guest portal.
      </p>
      <Textarea
        aria-label="Notes"
        rows={6}
        maxLength={4000}
        value={notes}
        disabled={!canWrite}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Returning guest, prefers a quiet room, left the place spotless…"
      />
      <FormError message={update.error instanceof ApiError ? update.error.message : update.error ? "Couldn't save the notes." : null} />
      {canWrite ? (
        <div className="flex justify-end">
          <Button type="submit" variant="primary" disabled={!dirty || update.isPending}>
            {update.isPending ? "Saving…" : "Save notes"}
          </Button>
        </div>
      ) : null}
    </form>
  );
}

export function GuestPreferencesForm({ guest, canWrite }: { guest: Guest; canWrite: boolean }) {
  const update = useUpdateGuest(guest.id);
  const [prefs, setPrefs] = useState<GuestPreferences>(guest.preferences as GuestPreferences);
  const set = <K extends keyof GuestPreferences>(key: K, value: GuestPreferences[K]) => setPrefs((p) => ({ ...p, [key]: value }));
  const text = (key: "preferred_language" | "bedding" | "accessibility" | "dietary" | "occasions" | "other") => prefs[key] ?? "";
  const save = (e: FormEvent) => {
    e.preventDefault();
    const clean = Object.fromEntries(Object.entries(prefs).filter(([, v]) => v !== "" && v !== undefined && v !== null)) as GuestPreferences;
    update.mutate({ preferences: clean, expected_version: guest.version }, { onSuccess: () => toast.success("Preferences saved") });
  };
  return (
    <form onSubmit={save} className="space-y-4 p-4" aria-label="Guest preferences">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Preferred language" htmlFor="pref-lang">
          <Input id="pref-lang" maxLength={35} disabled={!canWrite} value={text("preferred_language")} onChange={(e) => set("preferred_language", e.target.value)} />
        </Field>
        <Field label="Preferred contact" htmlFor="pref-contact">
          <Select id="pref-contact" disabled={!canWrite} value={prefs.contact_channel ?? ""} onChange={(e) => set("contact_channel", (e.target.value || undefined) as GuestPreferences["contact_channel"])}>
            <option value="">No preference</option>
            <option value="IN_APP">Guest portal</option>
            <option value="EMAIL">Email</option>
            <option value="SMS">SMS</option>
            <option value="WHATSAPP">WhatsApp</option>
            <option value="PHONE">Phone call</option>
          </Select>
        </Field>
        <Field label="Bedding" htmlFor="pref-bedding">
          <Input id="pref-bedding" maxLength={200} disabled={!canWrite} value={text("bedding")} onChange={(e) => set("bedding", e.target.value)} />
        </Field>
        <Field label="Accessibility" htmlFor="pref-access">
          <Input id="pref-access" maxLength={500} disabled={!canWrite} value={text("accessibility")} onChange={(e) => set("accessibility", e.target.value)} />
        </Field>
        <Field label="Dietary" htmlFor="pref-diet">
          <Input id="pref-diet" maxLength={200} disabled={!canWrite} value={text("dietary")} onChange={(e) => set("dietary", e.target.value)} />
        </Field>
        <Field label="Occasions" htmlFor="pref-occasion">
          <Input id="pref-occasion" maxLength={200} disabled={!canWrite} value={text("occasions")} onChange={(e) => set("occasions", e.target.value)} placeholder="Anniversary in October" />
        </Field>
      </div>
      <div className="flex flex-wrap gap-4 text-[13px]">
        <label className="flex items-center gap-2">
          <input type="checkbox" disabled={!canWrite} checked={Boolean(prefs.usually_requests_early_check_in)} onChange={(e) => set("usually_requests_early_check_in", e.target.checked || undefined)} />
          Usually asks for early check-in
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" disabled={!canWrite} checked={Boolean(prefs.usually_requests_late_checkout)} onChange={(e) => set("usually_requests_late_checkout", e.target.checked || undefined)} />
          Usually asks for late checkout
        </label>
      </div>
      <Field label="Anything else" htmlFor="pref-other">
        <Textarea id="pref-other" rows={2} maxLength={1000} disabled={!canWrite} value={text("other")} onChange={(e) => set("other", e.target.value)} />
      </Field>
      <FormError message={update.error instanceof ApiError ? update.error.message : update.error ? "Couldn't save preferences." : null} />
      {canWrite ? (
        <div className="flex justify-end">
          <Button type="submit" variant="primary" disabled={update.isPending}>
            {update.isPending ? "Saving…" : "Save preferences"}
          </Button>
        </div>
      ) : null}
    </form>
  );
}
