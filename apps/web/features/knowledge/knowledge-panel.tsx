"use client";

import {
  KNOWLEDGE_CATEGORIES,
  KNOWLEDGE_CATEGORY_LABEL,
  type KnowledgeCategory,
  type KnowledgeEntry,
  type KnowledgeVisibility,
  type Unit,
} from "@stayops/shared";
import { BookOpen, Eye, EyeOff, KeyRound, Pencil, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/form";
import { Panel } from "@/components/ui/panel";
import { Sheet } from "@/components/ui/sheet";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { ApiError } from "@/lib/api/client";

import { type KnowledgeInput, useArchiveKnowledge, useKnowledge, useSaveKnowledge } from "./api";

const VISIBILITY: Record<KnowledgeVisibility, { label: string; hint: string; tone: "success" | "accent" | "neutral"; icon: typeof Eye }> = {
  PUBLIC: { label: "Guests", hint: "Any guest with a portal link", tone: "success", icon: Eye },
  STAY: { label: "During stay", hint: "From the check-in day to check-out (codes, Wi-Fi)", tone: "accent", icon: KeyRound },
  INTERNAL: { label: "Team only", hint: "Never shown to guests or the guest assistant", tone: "neutral", icon: EyeOff },
};

function EntryForm({
  propertyId,
  units,
  entry,
  onDone,
}: {
  propertyId: string;
  units: Unit[];
  entry: KnowledgeEntry | null;
  onDone: () => void;
}) {
  const save = useSaveKnowledge(propertyId);
  const [form, setForm] = useState<KnowledgeInput>({
    category: entry?.category ?? "CHECK_IN",
    title: entry?.title ?? "",
    content: entry?.content ?? "",
    visibility: entry?.visibility ?? "PUBLIC",
    unit_id: entry?.unit_id ?? "",
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate(
      { id: entry?.id, body: { ...form, title: form.title.trim(), content: form.content.trim() }, version: entry?.version },
      { onSuccess: () => (toast.success(entry ? "Guide entry updated" : "Guide entry added"), onDone()) },
    );
  };
  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Guide entry form">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Category" htmlFor="kn-category">
          <Select id="kn-category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value as KnowledgeCategory })}>
            {KNOWLEDGE_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {KNOWLEDGE_CATEGORY_LABEL[c]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Who can see it" htmlFor="kn-visibility" hint={VISIBILITY[form.visibility].hint}>
          <Select id="kn-visibility" value={form.visibility} onChange={(e) => setForm({ ...form, visibility: e.target.value as KnowledgeVisibility })}>
            {(Object.keys(VISIBILITY) as KnowledgeVisibility[]).map((v) => (
              <option key={v} value={v}>
                {VISIBILITY[v].label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label="Title" htmlFor="kn-title">
        <Input id="kn-title" required maxLength={200} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Wi-Fi" />
      </Field>
      <Field label="Information" htmlFor="kn-content" hint="Write it the way a guest should read it. This is what the portal shows.">
        <Textarea id="kn-content" required rows={6} maxLength={4000} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} />
      </Field>
      {!entry ? (
        <Field label="Applies to" htmlFor="kn-unit">
          <Select id="kn-unit" value={form.unit_id ?? ""} onChange={(e) => setForm({ ...form, unit_id: e.target.value })}>
            <option value="">Every unit</option>
            {units.map((u) => (
              <option key={u.id} value={u.id}>
                Unit {u.name} only
              </option>
            ))}
          </Select>
        </Field>
      ) : null}
      <FormError message={save.error instanceof ApiError ? save.error.message : save.error ? "Couldn't save." : null} />
      <div className="flex justify-end gap-2">
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={save.isPending || !form.title.trim() || !form.content.trim()}>
          {save.isPending ? "Saving…" : entry ? "Save changes" : "Add entry"}
        </Button>
      </div>
    </form>
  );
}

/** Structured, authoritative guest information for one property (the guest portal's guide). */
export function KnowledgePanel({ propertyId, units, canWrite }: { propertyId: string; units: Unit[]; canWrite: boolean }) {
  const entries = useKnowledge(propertyId);
  const archive = useArchiveKnowledge();
  const [editing, setEditing] = useState<KnowledgeEntry | "new" | null>(null);
  const [removing, setRemoving] = useState<KnowledgeEntry | null>(null);

  const grouped = KNOWLEDGE_CATEGORIES.map((c) => ({ category: c, items: (entries.data ?? []).filter((e) => e.category === c) })).filter((g) => g.items.length);

  return (
    <Panel
      title="Guest guide"
      icon={<BookOpen />}
      count={entries.data?.length}
      description="What guests see in their portal — and the only source the guest assistant may answer from."
      actions={
        canWrite ? (
          <Button size="sm" onClick={() => setEditing("new")}>
            <Plus /> Add entry
          </Button>
        ) : null
      }
    >
      {entries.isPending ? (
        <SkeletonRows rows={4} columns={2} />
      ) : entries.error ? (
        <ErrorState compact error={entries.error} onRetry={() => entries.refetch()} />
      ) : grouped.length === 0 ? (
        <EmptyState compact icon={BookOpen} title="No guide entries yet" description="Start with check-in, Wi-Fi and house rules." />
      ) : (
        <div className="divide-y" data-testid="knowledge-list">
          {grouped.map((g) => (
            <section key={g.category} className="px-4 py-3">
              <h3 className="mb-2 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">{KNOWLEDGE_CATEGORY_LABEL[g.category]}</h3>
              <ul className="space-y-2.5">
                {g.items.map((e) => {
                  const v = VISIBILITY[e.visibility];
                  return (
                    <li key={e.id} className="group flex items-start gap-3" data-testid="knowledge-entry">
                      <div className="min-w-0 flex-1">
                        <p className="flex flex-wrap items-center gap-2 text-[13px] font-medium">
                          {e.title}
                          <Badge tone={v.tone}>
                            <v.icon className="size-3" /> {v.label}
                          </Badge>
                          {e.unit_name ? <Badge tone="outline">Unit {e.unit_name}</Badge> : null}
                        </p>
                        <p className="mt-0.5 line-clamp-3 text-xs whitespace-pre-line text-muted-foreground">{e.content}</p>
                      </div>
                      {canWrite ? (
                        <div className="flex shrink-0 gap-0.5 opacity-60 group-hover:opacity-100">
                          <Button variant="ghost" size="icon-sm" aria-label={`Edit ${e.title}`} onClick={() => setEditing(e)}>
                            <Pencil />
                          </Button>
                          <Button variant="ghost" size="icon-sm" aria-label={`Archive ${e.title}`} onClick={() => setRemoving(e)}>
                            <Trash2 />
                          </Button>
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}
      <Sheet open={editing !== null} onOpenChange={(o) => !o && setEditing(null)} title={editing === "new" ? "New guide entry" : "Edit guide entry"}>
        {editing !== null ? <EntryForm propertyId={propertyId} units={units} entry={editing === "new" ? null : editing} onDone={() => setEditing(null)} /> : null}
      </Sheet>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Archive “${removing?.title ?? ""}”?`}
        description="Guests will no longer see it. The change is recorded in the activity log."
        confirmLabel="Archive"
        pending={archive.isPending}
        onConfirm={() => removing && archive.mutate(removing.id, { onSuccess: () => setRemoving(null) })}
      />
    </Panel>
  );
}
