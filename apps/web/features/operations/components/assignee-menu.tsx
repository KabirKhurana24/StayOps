"use client";

import type { Role } from "@stayops/shared";
import { Check, UserMinus, UserPlus } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Avatar } from "@/components/ui/misc";
import { useStaff } from "@/features/operations/api";
import { plural } from "@/lib/format";

/**
 * Pick an organization member to own a task or work order, with live workload so
 * the operator can spread work by eye. Server validates membership and role.
 */
export function AssigneeMenu({
  value,
  onChange,
  disabled,
  pending,
  workload = "tasks",
  trigger,
  label = "Assign",
  roles,
}: {
  value: string | null;
  onChange: (assigneeId: string | null) => void;
  disabled?: boolean;
  pending?: boolean;
  workload?: "tasks" | "work_orders";
  trigger?: ReactNode;
  label?: string;
  /** Only offer members with these roles (e.g. the inbox is front-of-house only). */
  roles?: readonly Role[];
}) {
  const staff = useStaff();
  const members = roles ? staff.data?.filter((m) => roles.includes(m.role)) : staff.data;
  return (
    <Menu>
      <MenuTrigger asChild disabled={disabled || pending}>
        {trigger ?? (
          <Button size="sm" disabled={disabled || pending}>
            <UserPlus /> {value ? "Reassign" : label}
          </Button>
        )}
      </MenuTrigger>
      <MenuContent align="start" className="w-64" aria-label="Choose assignee">
        <MenuLabel>Team</MenuLabel>
        {staff.isPending ? <div className="px-2 py-1.5 text-xs text-muted-foreground">Loading team…</div> : null}
        {members?.map((m) => {
          const load = workload === "tasks" ? m.active_tasks : m.active_work_orders;
          return (
            <MenuItem key={m.user_id} onSelect={() => onChange(m.user_id)} data-testid="assignee-option" className="h-auto py-1.5">
              <Avatar name={m.name} size="xs" />
              <span className="min-w-0 flex-1">
                <span className="block truncate">{m.name}</span>
                <span className="block text-[11px] text-muted-foreground">
                  {plural(load, workload === "tasks" ? "active task" : "open work order")}
                  {workload === "tasks" && m.overdue_tasks ? ` · ${m.overdue_tasks} overdue` : ""}
                </span>
              </span>
              {m.user_id === value ? <Check className="text-accent!" /> : null}
            </MenuItem>
          );
        })}
        {value ? (
          <>
            <MenuSeparator />
            <MenuItem icon={<UserMinus />} onSelect={() => onChange(null)}>
              Unassign
            </MenuItem>
          </>
        ) : null}
      </MenuContent>
    </Menu>
  );
}
