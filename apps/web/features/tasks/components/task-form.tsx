"use client";

import { TASK_PRIORITIES, TASK_TYPES, type TaskPriority, type TaskType, zonedToUtcIso } from "@stayops/shared";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/form";
import { Sheet } from "@/components/ui/sheet";
import { useStaff } from "@/features/operations/api";
import { TASK_PRIORITY_LABEL, TASK_TYPE_LABEL } from "@/features/operations/format";
import { useProperties, useUnits } from "@/features/properties/api";
import { ApiError, newIdempotencyKey } from "@/lib/api/client";

import { useCreateTask } from "../api";

export function TaskFormSheet({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; onCreated?: (id: string) => void }) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="New task" description="Operational work for a property or unit.">
      {open ? (
        <TaskForm
          onDone={(id) => {
            onOpenChange(false);
            onCreated?.(id);
          }}
        />
      ) : null}
    </Sheet>
  );
}

function TaskForm({ onDone }: { onDone: (id: string) => void }) {
  const create = useCreateTask();
  const properties = useProperties();
  const staff = useStaff();
  const [propertyId, setPropertyId] = useState("");
  const units = useUnits(propertyId || undefined);
  const [idempotencyKey] = useState(() => newIdempotencyKey("task"));
  const [error, setError] = useState<string | null>(null);
  const tz = properties.data?.items.find((p) => p.id === propertyId)?.timezone ?? "UTC";

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const title = String(form.get("title") ?? "").trim();
    if (!title) return setError("Give the task a title.");
    if (!propertyId) return setError("Choose a property.");
    const unitId = String(form.get("unit_id") ?? "");
    const type = form.get("task_type") as TaskType;
    if (type === "TURNOVER_CLEANING" && !unitId) return setError("A turnover cleaning needs a unit.");
    const dueDate = String(form.get("due_date") ?? "");
    const dueTime = String(form.get("due_time") ?? "") || "15:00";
    const assignee = String(form.get("assigned_to") ?? "");
    setError(null);
    create.mutate(
      {
        idempotencyKey,
        body: {
          title,
          task_type: type,
          priority: form.get("priority") as TaskPriority,
          description: String(form.get("description") ?? "").trim() || undefined,
          property_id: propertyId,
          ...(unitId ? { unit_id: unitId } : {}),
          ...(assignee ? { assigned_to: assignee } : {}),
          // Due time is entered in the property's local time.
          ...(dueDate ? { due_at: zonedToUtcIso(dueDate, dueTime, tz) } : {}),
        },
      },
      {
        onSuccess: (task) => {
          toast.success(`Task created: ${task.title}`);
          onDone(task.id);
        },
        onError: (e) => setError(e instanceof ApiError ? e.message : "Could not create the task."),
      },
    );
  };

  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Task form">
      <FormError message={error} />
      <Field label="Title" htmlFor="task-title">
        <Input id="task-title" name="title" maxLength={200} placeholder="e.g. Replace hallway bulb" autoFocus />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Type" htmlFor="task-type">
          <Select id="task-type" name="task_type" defaultValue="OTHER">
            {TASK_TYPES.map((t) => (
              <option key={t} value={t}>
                {TASK_TYPE_LABEL[t]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Priority" htmlFor="task-priority">
          <Select id="task-priority" name="priority" defaultValue="NORMAL">
            {TASK_PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {TASK_PRIORITY_LABEL[p]}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Property" htmlFor="task-property">
          <Select id="task-property" value={propertyId} onChange={(e) => setPropertyId(e.target.value)}>
            <option value="">Choose…</option>
            {properties.data?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Unit" htmlFor="task-unit">
          <Select id="task-unit" name="unit_id" disabled={!propertyId} defaultValue="">
            <option value="">Whole property</option>
            {units.data?.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Due date" htmlFor="task-due-date" hint={propertyId ? `Local time · ${tz}` : undefined}>
          <Input id="task-due-date" name="due_date" type="date" />
        </Field>
        <Field label="Due time" htmlFor="task-due-time">
          <Input id="task-due-time" name="due_time" type="time" defaultValue="15:00" />
        </Field>
      </div>
      <Field label="Assignee" htmlFor="task-assignee">
        <Select id="task-assignee" name="assigned_to" defaultValue="">
          <option value="">Unassigned</option>
          {staff.data?.map((m) => (
            <option key={m.user_id} value={m.user_id}>
              {m.name} · {m.active_tasks} active
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Description" htmlFor="task-description">
        <Textarea id="task-description" name="description" rows={3} maxLength={4000} />
      </Field>
      <div className="flex justify-end pt-2">
        <Button type="submit" variant="primary" disabled={create.isPending}>
          {create.isPending ? "Saving…" : "Create task"}
        </Button>
      </div>
    </form>
  );
}
