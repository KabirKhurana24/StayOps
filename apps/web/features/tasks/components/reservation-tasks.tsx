"use client";

import { Bot } from "lucide-react";
import Link from "next/link";

import { Skeleton } from "@/components/ui/states";
import { CleaningStatusBadge, TaskStatusBadge } from "@/features/operations/components/badges";
import { TASK_TYPE_LABEL } from "@/features/operations/format";
import { can, useCurrentOrganization } from "@/features/organizations/api";

import { useTasks } from "../api";

/**
 * Operational work linked to a stay (e.g. the turnover cleaning automation creates
 * when the guest checks out). Realtime keeps it current via the tasks query keys.
 */
export function ReservationTasks({ reservationId }: { reservationId: string }) {
  const org = useCurrentOrganization();
  const allowed = can(org.data, "tasks:read");
  const tasks = useTasks({ reservation_id: reservationId, sort: "-created_at", limit: 20 }, { enabled: allowed });
  if (!allowed) return null;
  if (tasks.isPending) return <Skeleton className="h-9 w-full" />;
  const items = tasks.data?.items ?? [];
  if (items.length === 0) {
    return <p className="text-[13px] text-subtle-foreground">No tasks yet. A turnover cleaning is created automatically at check-out.</p>;
  }
  return (
    <ul className="divide-y rounded-md border" aria-label="Reservation tasks">
      {items.map((t) => (
        <li key={t.id}>
          <Link
            href={t.cleaning ? `/cleaning?view=all&c=${t.cleaning.id}` : `/tasks?t=${t.id}`}
            className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted/40"
            data-testid="reservation-task"
            data-task-type={t.task_type}
            data-status={t.status}
          >
            {t.automated ? <Bot className="size-3.5 shrink-0 text-muted-foreground" aria-label="Created by automation" /> : null}
            <span className="min-w-0 flex-1">
              <span className="block truncate">{t.title}</span>
              <span className="block text-[11px] text-muted-foreground">
                {TASK_TYPE_LABEL[t.task_type]}
                {t.assignee ? ` · ${t.assignee.name}` : " · Unassigned"}
              </span>
            </span>
            {t.cleaning ? <CleaningStatusBadge status={t.cleaning.status} /> : <TaskStatusBadge status={t.status} />}
          </Link>
        </li>
      ))}
    </ul>
  );
}
