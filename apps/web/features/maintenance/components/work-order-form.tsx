"use client";

import { MAINTENANCE_CATEGORIES, type MaintenanceCategory, TASK_PRIORITIES, type TaskPriority } from "@stayops/shared";
import { Lock } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/form";
import { Sheet } from "@/components/ui/sheet";
import { useStaff } from "@/features/operations/api";
import { MAINTENANCE_CATEGORY_LABEL, TASK_PRIORITY_LABEL } from "@/features/operations/format";
import { useProperties, useUnits } from "@/features/properties/api";
import { ApiError, newIdempotencyKey } from "@/lib/api/client";

import { useCreateWorkOrder } from "../api";

export type IssueSource = {
  taskId: string;
  taskTitle: string;
  unitId: string | null;
  unitName: string | null;
  propertyName: string;
};

/**
 * Report an issue / open a work order. From a task (e.g. a cleaner finding a broken
 * AC) the unit and reservation come from the task, preserving traceability.
 */
export function WorkOrderFormSheet({
  open,
  onOpenChange,
  source,
  canAssign,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source?: IssueSource;
  canAssign: boolean;
  onCreated?: (id: string) => void;
}) {
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={source ? "Report an issue" : "New work order"}
      description={source ? `Found during “${source.taskTitle}” · ${source.propertyName}${source.unitName ? ` · Unit ${source.unitName}` : ""}` : "Log a maintenance issue for a unit."}
    >
      {open ? <WorkOrderForm source={source} canAssign={canAssign} onDone={(id) => (onOpenChange(false), onCreated?.(id))} /> : null}
    </Sheet>
  );
}

function WorkOrderForm({ source, canAssign, onDone }: { source?: IssueSource; canAssign: boolean; onDone: (id: string) => void }) {
  const create = useCreateWorkOrder();
  const properties = useProperties();
  const staff = useStaff({ enabled: canAssign });
  const [propertyId, setPropertyId] = useState("");
  const units = useUnits(propertyId || undefined);
  const [idempotencyKey] = useState(() => newIdempotencyKey("work-order"));
  const [error, setError] = useState<string | null>(null);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const title = String(form.get("title") ?? "").trim();
    const unitId = source?.unitId ?? String(form.get("unit_id") ?? "");
    if (!title) return setError("Describe the issue in a few words.");
    if (!source && !unitId) return setError("Choose the unit with the issue.");
    setError(null);
    const assignee = String(form.get("assigned_to") ?? "");
    create.mutate(
      {
        idempotencyKey,
        body: {
          title,
          description: String(form.get("description") ?? "").trim() || undefined,
          category: form.get("category") as MaintenanceCategory,
          priority: form.get("priority") as TaskPriority,
          blocks_unit: form.get("blocks_unit") === "on",
          ...(source ? { source_task_id: source.taskId } : { unit_id: unitId }),
          ...(assignee ? { assigned_to: assignee } : {}),
        },
      },
      {
        onSuccess: (wo) => {
          toast.success(`Work order created: ${wo.title}`, {
            description: wo.unit_blocked ? `Unit ${wo.unit.name} is blocked for new bookings` : `${wo.property.name} · Unit ${wo.unit.name}`,
          });
          onDone(wo.id);
        },
        onError: (e) => setError(e instanceof ApiError ? e.message : "Could not create the work order."),
      },
    );
  };

  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Work order form">
      <FormError message={error} />
      <Field label="Issue" htmlFor="wo-title">
        <Input id="wo-title" name="title" placeholder="e.g. AC not working" maxLength={200} autoFocus />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Category" htmlFor="wo-category">
          <Select id="wo-category" name="category" defaultValue="OTHER">
            {MAINTENANCE_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {MAINTENANCE_CATEGORY_LABEL[c]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Priority" htmlFor="wo-priority">
          <Select id="wo-priority" name="priority" defaultValue="NORMAL">
            {TASK_PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {TASK_PRIORITY_LABEL[p]}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {source ? null : (
        <div className="grid grid-cols-2 gap-3">
          <Field label="Property" htmlFor="wo-property">
            <Select id="wo-property" value={propertyId} onChange={(e) => setPropertyId(e.target.value)}>
              <option value="">Choose…</option>
              {properties.data?.items.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Unit" htmlFor="wo-unit">
            <Select id="wo-unit" name="unit_id" disabled={!propertyId} defaultValue="">
              <option value="">{propertyId ? "Choose…" : "Pick a property"}</option>
              {units.data?.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      )}
      <Field label="Details" htmlFor="wo-description" hint="What you saw, where, and anything the technician should bring.">
        <Textarea id="wo-description" name="description" maxLength={4000} rows={3} />
      </Field>
      <label className="flex cursor-pointer items-start gap-2.5 rounded-md border bg-surface-raised px-3 py-2.5">
        <input type="checkbox" name="blocks_unit" className="mt-0.5 size-3.5 accent-[var(--danger)]" aria-label="Block unit for new bookings" />
        <span className="text-[13px]">
          <span className="flex items-center gap-1.5 font-medium">
            <Lock className="size-3.5 text-danger" aria-hidden /> Block unit for new bookings
          </span>
          <span className="block text-xs text-muted-foreground">The unit is unavailable until this work order is resolved or closed.</span>
        </span>
      </label>
      {canAssign ? (
        <Field label="Technician" htmlFor="wo-assignee">
          <Select id="wo-assignee" name="assigned_to" defaultValue="">
            <option value="">Assign later</option>
            {staff.data?.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.name} · {m.active_work_orders} open
              </option>
            ))}
          </Select>
        </Field>
      ) : null}
      <div className="flex justify-end gap-2 pt-2">
        <Button type="submit" variant="primary" disabled={create.isPending}>
          {create.isPending ? "Saving…" : source ? "Report issue" : "Create work order"}
        </Button>
      </div>
    </form>
  );
}
