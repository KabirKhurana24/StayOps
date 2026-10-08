"use client";

import type { Task } from "@stayops/shared";
import { Bot } from "lucide-react";

import { Avatar } from "@/components/ui/misc";
import { Table, TableScroll, Td, Th, THead, Tr } from "@/components/ui/table";
import { DueLabel, PriorityLabel, TaskStatusBadge } from "@/features/operations/components/badges";
import { CLEANING_STATUS_LABEL, TASK_TYPE_LABEL } from "@/features/operations/format";
import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { formatInZone } from "@/lib/format";
import { cn } from "@/lib/utils";

function Assignee({ task }: { task: Task }) {
  if (!task.assignee) return <span className="text-xs text-subtle-foreground">Unassigned</span>;
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5" data-testid="task-assignee">
      <Avatar name={task.assignee.name} size="xs" />
      <span className="truncate">{task.assignee.name}</span>
    </span>
  );
}

function Row({ task, now, onOpen }: { task: Task; now: number; onOpen: (id: string) => void }) {
  const fresh = useRecentlyChanged(task.id);
  const done = task.status === "COMPLETED" || task.status === "CANCELLED";
  const tz = task.property.timezone;
  return (
    <Tr
      interactive
      tabIndex={0}
      onClick={() => onOpen(task.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onOpen(task.id);
      }}
      className={cn(fresh && "animate-row-in", done && "text-muted-foreground")}
      data-testid="task-row"
      data-task-id={task.id}
      data-status={task.status}
      data-priority={task.priority}
      data-assignee={task.assignee?.name ?? ""}
    >
      <Td className="w-[92px]">
        <PriorityLabel priority={task.priority} />
      </Td>
      <Td className="max-w-[340px]">
        <div className={cn("truncate font-medium", task.status === "CANCELLED" && "line-through decoration-subtle-foreground/50")}>{task.title}</div>
        <div className="flex items-center gap-1.5 truncate text-xs text-muted-foreground">
          {task.automated ? <Bot className="size-3 shrink-0" aria-label="Created by automation" /> : null}
          <span>{TASK_TYPE_LABEL[task.task_type]}</span>
          {task.cleaning ? (
            <span>
              · {CLEANING_STATUS_LABEL[task.cleaning.status]} · {task.cleaning.checklist_done}/{task.cleaning.checklist_total}
            </span>
          ) : null}
          {task.reservation ? <span className="font-mono text-[11px]">· {task.reservation.confirmation_code}</span> : null}
        </div>
      </Td>
      <Td className="max-w-[200px]">
        <div className="truncate">{task.property.name}</div>
        <div className="text-xs text-muted-foreground">{task.unit ? `Unit ${task.unit.name}` : "Whole property"}</div>
      </Td>
      <Td className="max-w-[180px]">
        <Assignee task={task} />
      </Td>
      <Td className="whitespace-nowrap">
        <DueLabel dueAt={task.due_at} now={now} done={done} />
        {task.due_at ? (
          <div className="text-[11px] text-subtle-foreground tabular">
            {formatInZone(task.due_at, tz, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
          </div>
        ) : null}
      </Td>
      <Td>
        <TaskStatusBadge status={task.status} />
      </Td>
    </Tr>
  );
}

export function TaskTable({ rows, onOpen, now }: { rows: Task[]; onOpen: (id: string) => void; now: number }) {
  return (
    <TableScroll>
      <Table className="min-w-[900px]">
        <THead>
          <tr>
            <Th>Priority</Th>
            <Th>Task</Th>
            <Th>Property · Unit</Th>
            <Th>Assignee</Th>
            <Th>Due</Th>
            <Th>Status</Th>
          </tr>
        </THead>
        <tbody>
          {rows.map((t) => (
            <Row key={t.id} task={t} now={now} onOpen={onOpen} />
          ))}
        </tbody>
      </Table>
    </TableScroll>
  );
}
