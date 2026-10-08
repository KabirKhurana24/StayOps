"use client";

import type { Cleaning } from "@stayops/shared";
import { AlertTriangle, BadgeCheck, CircleX, Lock, Wrench } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/form";
import { Meter } from "@/components/ui/misc";
import { MaintenanceStatusBadge } from "@/features/operations/components/badges";
import { WorkOrderFormSheet } from "@/features/maintenance/components/work-order-form";
import { cn } from "@/lib/utils";

import { useInspection, useUpdateCleaning } from "../api";

const FINISHED = new Set(["COMPLETED", "INSPECTION_REQUIRED", "APPROVED"]);

/** Checklist + notes + inspection + issue reporting. Shared by the task and cleaning drawers. */
export function CleaningPanel({ cleaning, canWork, canManage }: { cleaning: Cleaning; canWork: boolean; canManage: boolean }) {
  const update = useUpdateCleaning(cleaning.id);
  const inspection = useInspection(cleaning.id);
  const [reporting, setReporting] = useState(false);
  const [failing, setFailing] = useState(false);
  const [failNotes, setFailNotes] = useState("");
  const [notes, setNotes] = useState(cleaning.cleaner_notes ?? "");
  const taskOpen = cleaning.task.status === "TODO" || cleaning.task.status === "IN_PROGRESS";
  const editable = canWork && taskOpen && !FINISHED.has(cleaning.status);
  const awaiting = cleaning.status === "COMPLETED" || cleaning.status === "INSPECTION_REQUIRED";
  const pct = cleaning.checklist_total ? cleaning.checklist_done / cleaning.checklist_total : 0;

  return (
    <div className="space-y-4" data-testid="cleaning-panel" data-cleaning-status={cleaning.status}>
      {cleaning.status === "FAILED" ? (
        <div role="alert" className="flex gap-2 rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-[13px]">
          <CircleX className="mt-0.5 size-3.5 shrink-0 text-danger" aria-hidden />
          <div>
            <p className="font-medium text-danger">Inspection failed — re-clean needed</p>
            {cleaning.inspection_notes ? <p className="text-xs text-muted-foreground">{cleaning.inspection_notes}</p> : null}
          </div>
        </div>
      ) : null}

      <div>
        <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
          <span>Checklist</span>
          <span className="tabular" data-testid="checklist-progress">
            {cleaning.checklist_done}/{cleaning.checklist_total}
          </span>
        </div>
        <Meter value={pct} label="Checklist progress" className="mb-2" />
        <ul className="divide-y rounded-md border" aria-label="Cleaning checklist">
          {cleaning.checklist.map((item) => (
            <li key={item.id}>
              <label
                className={cn(
                  "flex items-center gap-2.5 px-3 py-2 text-[13px]",
                  editable ? "cursor-pointer hover:bg-muted/40" : "cursor-default",
                  item.completed && "text-muted-foreground",
                )}
              >
                <input
                  type="checkbox"
                  className="size-3.5 accent-[var(--accent)]"
                  checked={item.completed}
                  disabled={!editable}
                  aria-label={item.label}
                  data-testid="checklist-item"
                  onChange={(e) => update.mutate({ checklist: [{ id: item.id, completed: e.target.checked }] })}
                />
                <span className={cn(item.completed && "line-through decoration-subtle-foreground/50")}>{item.label}</span>
              </label>
            </li>
          ))}
        </ul>
        {editable && cleaning.checklist_done < cleaning.checklist_total ? (
          <Button
            size="xs"
            variant="ghost"
            className="mt-1.5 text-muted-foreground"
            onClick={() => update.mutate({ checklist: cleaning.checklist.map((i) => ({ id: i.id, completed: true })) })}
          >
            Mark all done
          </Button>
        ) : null}
      </div>

      <div>
        <label htmlFor={`notes-${cleaning.id}`} className="mb-1.5 block text-xs text-muted-foreground">
          Cleaner notes
        </label>
        <Textarea
          id={`notes-${cleaning.id}`}
          rows={2}
          value={notes}
          disabled={!editable}
          placeholder={editable ? "Anything the next person should know" : "No notes"}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() => {
            if ((cleaning.cleaner_notes ?? "") !== notes) update.mutate({ cleaner_notes: notes || null });
          }}
        />
      </div>

      {awaiting && canManage ? (
        <div className="rounded-md border bg-surface-raised p-3" data-testid="inspection">
          <p className="mb-2 text-[13px] font-medium">Inspection</p>
          {failing ? (
            <div className="space-y-2">
              <Textarea rows={2} value={failNotes} onChange={(e) => setFailNotes(e.target.value)} placeholder="What needs redoing?" aria-label="Inspection notes" autoFocus />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="danger"
                  disabled={!failNotes.trim() || inspection.isPending}
                  onClick={() =>
                    inspection.mutate(
                      { decision: "fail", notes: failNotes.trim() },
                      { onSuccess: () => (setFailing(false), toast.success("Sent back for a re-clean")) },
                    )
                  }
                >
                  Fail inspection
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setFailing(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="primary"
                disabled={inspection.isPending}
                onClick={() => inspection.mutate({ decision: "approve" }, { onSuccess: () => toast.success("Approved — unit ready") })}
              >
                <BadgeCheck /> Approve
              </Button>
              <Button size="sm" variant="danger" onClick={() => setFailing(true)}>
                <CircleX /> Fail
              </Button>
            </div>
          )}
        </div>
      ) : null}

      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-xs text-muted-foreground">Issues found</span>
          {canWork ? (
            <Button size="xs" onClick={() => setReporting(true)}>
              <AlertTriangle /> Report issue
            </Button>
          ) : null}
        </div>
        {cleaning.work_orders.length === 0 ? (
          <p className="text-xs text-subtle-foreground">None reported.</p>
        ) : (
          <ul className="divide-y rounded-md border" data-testid="related-work-orders">
            {cleaning.work_orders.map((wo) => (
              <li key={wo.id}>
                <Link href={`/maintenance?wo=${wo.id}`} className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted/40">
                  <Wrench className="size-3.5 text-muted-foreground" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">{wo.title}</span>
                  {wo.blocks_unit && (wo.status !== "RESOLVED" && wo.status !== "CLOSED") ? (
                    <Lock className="size-3.5 text-danger" aria-label="Blocks the unit" />
                  ) : null}
                  <MaintenanceStatusBadge status={wo.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      <WorkOrderFormSheet
        open={reporting}
        onOpenChange={setReporting}
        canAssign={canManage}
        source={{
          taskId: cleaning.task.id,
          taskTitle: cleaning.task.title,
          unitId: cleaning.task.unit?.id ?? null,
          unitName: cleaning.task.unit?.name ?? null,
          propertyName: cleaning.task.property.name,
        }}
      />
    </div>
  );
}
