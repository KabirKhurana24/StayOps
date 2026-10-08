"use client";

import {
  GUEST_REQUEST_LABEL,
  GUEST_REQUEST_TYPES,
  type GuestRequest,
  type GuestRequestType,
  MAINTENANCE_CATEGORIES,
  type MaintenanceCategory,
  TASK_PRIORITIES,
  type TaskPriority,
  zonedToUtcIso,
} from "@stayops/shared";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/form";
import { useConvertToMaintenance, useConvertToTask, useCreateGuestRequest } from "@/features/guest-requests/api";
import { useStaff } from "@/features/operations/api";
import { ApiError } from "@/lib/api/client";

const PRIORITY_LABEL: Record<TaskPriority, string> = { LOW: "Low", NORMAL: "Normal", HIGH: "High", URGENT: "Urgent" };
const CATEGORY_LABEL: Record<MaintenanceCategory, string> = {
  HVAC: "Heating / AC",
  PLUMBING: "Plumbing",
  ELECTRICAL: "Electrical",
  APPLIANCE: "Appliance",
  LOCK: "Lock / access",
  FURNITURE: "Furniture",
  CLEANING: "Cleaning",
  OTHER: "Other",
};

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : e ? "Something went wrong." : null);

/** Raise a guest request from the conversation (e.g. the guest asked in chat). */
export function CreateRequestDialog({
  open,
  onOpenChange,
  conversationId,
  timeZone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  conversationId: string;
  timeZone: string;
}) {
  const create = useCreateGuestRequest();
  const [type, setType] = useState<GuestRequestType>("HOUSEKEEPING");
  const [priority, setPriority] = useState<TaskPriority>("NORMAL");
  const [description, setDescription] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const wantsTime = type === "EARLY_CHECK_IN" || type === "LATE_CHECKOUT";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      {
        conversation_id: conversationId,
        type,
        priority,
        description: description.trim(),
        requested_for: wantsTime && date && time ? zonedToUtcIso(date, time, timeZone) : undefined,
      },
      {
        onSuccess: (r) => {
          toast.success(`${GUEST_REQUEST_LABEL[r.type]} request created`);
          setDescription("");
          onOpenChange(false);
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="New guest request" description="Track what the guest asked for until it's done.">
      <form onSubmit={submit} className="space-y-4 px-5 py-4" aria-label="Guest request form">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Request" htmlFor="req-type">
            <Select id="req-type" value={type} onChange={(e) => setType(e.target.value as GuestRequestType)}>
              {GUEST_REQUEST_TYPES.map((t) => (
                <option key={t} value={t}>
                  {GUEST_REQUEST_LABEL[t]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Priority" htmlFor="req-priority">
            <Select id="req-priority" value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
              {TASK_PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {PRIORITY_LABEL[p]}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="Details" htmlFor="req-description">
          <Textarea id="req-description" rows={3} required maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Two extra bath towels" />
        </Field>
        {wantsTime ? (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Requested date" htmlFor="req-date" hint={`Property time (${timeZone})`}>
              <Input id="req-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <Field label="Time" htmlFor="req-time">
              <Input id="req-time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
            </Field>
          </div>
        ) : null}
        <FormError message={errorText(create.error)} />
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={create.isPending || !description.trim()}>
            {create.isPending ? "Creating…" : "Create request"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Request → operations task (one per request; the server links them). */
export function ConvertToTaskDialog({
  request,
  open,
  onOpenChange,
}: {
  request: GuestRequest;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const convert = useConvertToTask(request.id);
  const staff = useStaff({ enabled: open });
  const [title, setTitle] = useState(`${GUEST_REQUEST_LABEL[request.type]} · Unit ${request.unit.name}`);
  const [priority, setPriority] = useState<TaskPriority>(request.priority);
  const [assignee, setAssignee] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    convert.mutate(
      { title: title.trim(), priority, assigned_to: assignee || undefined },
      {
        onSuccess: () => {
          toast.success("Task created and linked to the request");
          onOpenChange(false);
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Create task" description={`From the guest's request: “${request.description}”`}>
      <form onSubmit={submit} className="space-y-4 px-5 py-4" aria-label="Convert to task form">
        <Field label="Task" htmlFor="task-title">
          <Input id="task-title" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Priority" htmlFor="task-priority">
            <Select id="task-priority" value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
              {TASK_PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {PRIORITY_LABEL[p]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Assign to" htmlFor="task-assignee">
            <Select id="task-assignee" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
              <option value="">Unassigned</option>
              {staff.data?.map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.name} · {m.active_tasks} active
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <FormError message={errorText(convert.error)} />
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={convert.isPending || !title.trim()}>
            {convert.isPending ? "Creating…" : "Create task"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Request → maintenance work order (one per request; guest-reported faults default to high). */
export function ConvertToMaintenanceDialog({
  request,
  open,
  onOpenChange,
}: {
  request: GuestRequest;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const convert = useConvertToMaintenance(request.id);
  const [title, setTitle] = useState(request.type === "MAINTENANCE" ? request.description.slice(0, 120) : GUEST_REQUEST_LABEL[request.type]);
  const [category, setCategory] = useState<MaintenanceCategory>("OTHER");
  const [priority, setPriority] = useState<TaskPriority>(request.priority === "URGENT" ? "URGENT" : "HIGH");
  const [blocksUnit, setBlocksUnit] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    convert.mutate(
      { title: title.trim(), category, priority, blocks_unit: blocksUnit },
      {
        onSuccess: () => {
          toast.success("Work order created and linked to the request");
          onOpenChange(false);
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Create maintenance work order" description={`Unit ${request.unit.name} · ${request.property.name}`}>
      <form onSubmit={submit} className="space-y-4 px-5 py-4" aria-label="Convert to maintenance form">
        <Field label="Issue" htmlFor="wo-title">
          <Input id="wo-title" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Category" htmlFor="wo-category">
            <Select id="wo-category" value={category} onChange={(e) => setCategory(e.target.value as MaintenanceCategory)}>
              {MAINTENANCE_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Priority" htmlFor="wo-priority">
            <Select id="wo-priority" value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
              {TASK_PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {PRIORITY_LABEL[p]}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <label className="flex items-start gap-2 text-[13px]">
          <input type="checkbox" className="mt-0.5" checked={blocksUnit} onChange={(e) => setBlocksUnit(e.target.checked)} />
          <span>
            Take the unit out of availability
            <span className="block text-xs text-muted-foreground">Only if it can&apos;t be booked until fixed. The current guest&apos;s stay is not affected.</span>
          </span>
        </label>
        <FormError message={errorText(convert.error)} />
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={convert.isPending || !title.trim()}>
            {convert.isPending ? "Creating…" : "Create work order"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
