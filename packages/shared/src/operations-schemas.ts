/**
 * Operations contract (Phase 3): tasks, cleanings, maintenance work orders,
 * staff workload and unit operational state. Parsed at runtime by the web app.
 */
import { z } from "zod";

import {
  CLEANING_STATUSES,
  MAINTENANCE_CATEGORIES,
  MAINTENANCE_STATUSES,
  RESERVATION_STATUSES,
  ROLES,
  TASK_PRIORITIES,
  TASK_STATUSES,
  TASK_TYPES,
  UNIT_OPERATIONAL_STATUSES,
} from "./enums";

const uuid = z.uuid();
const isoDateTime = z.iso.datetime({ offset: true });

const propertyRef = z.object({ id: uuid, name: z.string(), timezone: z.string() });
const unitRef = z.object({ id: uuid, name: z.string() });
const memberRef = z.object({ id: uuid, name: z.string(), email: z.string().nullable().optional() });
const reservationBrief = z.object({
  id: uuid,
  confirmation_code: z.string(),
  status: z.enum(RESERVATION_STATUSES),
  check_in: isoDateTime,
  check_out: isoDateTime,
  guest_name: z.string(),
});

export const taskSchema = z.object({
  id: uuid,
  organization_id: uuid,
  task_type: z.enum(TASK_TYPES),
  title: z.string(),
  description: z.string().nullable(),
  status: z.enum(TASK_STATUSES),
  priority: z.enum(TASK_PRIORITIES),
  property: propertyRef,
  unit: unitRef.nullable(),
  reservation: reservationBrief.nullable(),
  assignee: memberRef.nullable(),
  due_at: isoDateTime.nullable(),
  started_at: isoDateTime.nullable(),
  completed_at: isoDateTime.nullable(),
  cancelled_at: isoDateTime.nullable(),
  created_by: uuid.nullable(),
  created_by_label: z.string().nullable(),
  automated: z.boolean(),
  overdue: z.boolean(),
  cleaning: z
    .object({
      id: uuid,
      status: z.enum(CLEANING_STATUSES),
      checklist_done: z.number().int(),
      checklist_total: z.number().int(),
    })
    .nullable(),
  version: z.number().int(),
  created_at: isoDateTime,
  updated_at: isoDateTime,
});
export type Task = z.infer<typeof taskSchema>;

export const taskCountsSchema = z.object({
  all: z.number().int(),
  mine: z.number().int(),
  today: z.number().int(),
  overdue: z.number().int(),
  completed: z.number().int(),
});
export type TaskCounts = z.infer<typeof taskCountsSchema>;

export const staffMemberSchema = z.object({
  user_id: uuid,
  name: z.string(),
  email: z.string().nullable(),
  role: z.enum(ROLES),
  active_tasks: z.number().int(),
  overdue_tasks: z.number().int(),
  active_work_orders: z.number().int(),
});
export type StaffMember = z.infer<typeof staffMemberSchema>;

export const checklistItemSchema = z.object({ id: z.string(), label: z.string(), completed: z.boolean() });
export type ChecklistItem = z.infer<typeof checklistItemSchema>;

export const cleaningSchema = z.object({
  id: uuid,
  task_id: uuid,
  status: z.enum(CLEANING_STATUSES),
  checklist: z.array(checklistItemSchema),
  checklist_done: z.number().int(),
  checklist_total: z.number().int(),
  cleaner_notes: z.string().nullable(),
  inspection_notes: z.string().nullable(),
  started_at: isoDateTime.nullable(),
  completed_at: isoDateTime.nullable(),
  approved_at: isoDateTime.nullable(),
  failed_at: isoDateTime.nullable(),
  failure_count: z.number().int(),
  version: z.number().int(),
  task: taskSchema,
  departure: z
    .object({ reservation_id: uuid, confirmation_code: z.string(), guest_name: z.string(), check_out: isoDateTime })
    .nullable(),
  next_arrival: z
    .object({ reservation_id: uuid, confirmation_code: z.string(), guest_name: z.string(), check_in: isoDateTime })
    .nullable(),
  /** Computed by the backend from live reservations; thresholds are backend configuration. */
  turnover: z.object({
    checkout_at: isoDateTime.nullable(),
    next_check_in_at: isoDateTime.nullable(),
    minutes: z.number().int().nullable(),
    tight: z.boolean(),
    same_day: z.boolean(),
    threshold_minutes: z.number().int(),
  }),
  work_orders: z.array(
    z.object({
      id: uuid,
      title: z.string(),
      status: z.enum(MAINTENANCE_STATUSES),
      priority: z.enum(TASK_PRIORITIES),
      blocks_unit: z.boolean(),
    }),
  ),
  created_at: isoDateTime,
  updated_at: isoDateTime,
});
export type Cleaning = z.infer<typeof cleaningSchema>;

export const cleaningCountsSchema = z.object({
  today: z.number().int(),
  upcoming: z.number().int(),
  completed: z.number().int(),
  inspection: z.number().int(),
});
export type CleaningCounts = z.infer<typeof cleaningCountsSchema>;

export const guestImpactSchema = z.object({
  reservation_id: uuid,
  confirmation_code: z.string(),
  guest_name: z.string(),
  status: z.enum(RESERVATION_STATUSES),
  check_in: isoDateTime,
  check_out: isoDateTime,
  in_house: z.boolean(),
});

export const workOrderSchema = z.object({
  id: uuid,
  organization_id: uuid,
  title: z.string(),
  description: z.string().nullable(),
  category: z.enum(MAINTENANCE_CATEGORIES),
  priority: z.enum(TASK_PRIORITIES),
  status: z.enum(MAINTENANCE_STATUSES),
  blocks_unit: z.boolean(),
  unit_blocked: z.boolean(),
  property: propertyRef,
  unit: unitRef,
  reservation: reservationBrief.nullable(),
  source_task: z
    .object({
      id: uuid,
      title: z.string(),
      task_type: z.enum(TASK_TYPES),
      status: z.enum(TASK_STATUSES),
      cleaning_id: uuid.nullable().optional(),
    })
    .nullable(),
  assignee: memberRef.nullable(),
  reported_by: uuid.nullable(),
  reported_by_label: z.string().nullable(),
  blocked_reason: z.string().nullable(),
  resolution_notes: z.string().nullable(),
  started_at: isoDateTime.nullable(),
  resolved_at: isoDateTime.nullable(),
  closed_at: isoDateTime.nullable(),
  version: z.number().int(),
  created_at: isoDateTime,
  updated_at: isoDateTime,
  guest_impact: z.array(guestImpactSchema).nullable().optional(),
});
export type WorkOrder = z.infer<typeof workOrderSchema>;

export const workOrderCountsSchema = z.object({
  all: z.number().int(),
  open: z.number().int(),
  assigned: z.number().int(),
  in_progress: z.number().int(),
  blocked: z.number().int(),
  resolved: z.number().int(),
  active: z.number().int(),
  blocking_units: z.number().int(),
});
export type WorkOrderCounts = z.infer<typeof workOrderCountsSchema>;

export const unitStateSchema = z.object({
  unit_id: uuid,
  property_id: uuid,
  name: z.string(),
  status: z.enum(UNIT_OPERATIONAL_STATUSES),
  bookable: z.boolean(),
  maintenance_blocked: z.boolean(),
  blocking_work_order_ids: z.array(uuid),
});
export type UnitState = z.infer<typeof unitStateSchema>;
