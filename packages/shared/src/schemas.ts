/**
 * Runtime-validated API contract. The web app parses every API response with
 * these schemas, so backend/frontend drift fails loudly instead of rendering
 * garbage.
 */
import { z } from "zod";

import {
  ACTOR_TYPES,
  EVENT_STATUSES,
  PERMISSIONS,
  PROPERTY_STATUSES,
  RESERVATION_SOURCES,
  RESERVATION_STATUSES,
  ROLES,
  SIGNAL_SEVERITIES,
  UNIT_OPERATIONAL_STATUSES,
  UNIT_STATUSES,
  UNIT_TYPES,
} from "./enums";

const uuid = z.uuid();
const isoDateTime = z.iso.datetime({ offset: true });
const money = z.string().regex(/^\d+(\.\d{1,2})?$/);

export const pageSchema = <T extends z.ZodTypeAny>(item: T) =>
  z.object({
    items: z.array(item),
    total: z.number().int(),
    limit: z.number().int(),
    offset: z.number().int(),
  });
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

export const apiErrorSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    request_id: z.string().nullable().optional(),
    details: z.unknown().optional(),
  }),
});
export type ApiErrorBody = z.infer<typeof apiErrorSchema>;

export const organizationSchema = z.object({
  id: uuid,
  name: z.string(),
  slug: z.string(),
  default_currency: z.string(),
  created_at: isoDateTime,
});
export type Organization = z.infer<typeof organizationSchema>;

export const membershipSchema = z.object({
  organization: organizationSchema,
  role: z.enum(ROLES),
  joined_at: isoDateTime,
});
export type Membership = z.infer<typeof membershipSchema>;

export const meSchema = z.object({
  id: uuid,
  email: z.string().nullable(),
  full_name: z.string().nullable(),
  memberships: z.array(membershipSchema),
});
export type Me = z.infer<typeof meSchema>;

export const currentOrganizationSchema = z.object({
  organization: organizationSchema,
  role: z.enum(ROLES),
  permissions: z.array(z.enum(PERMISSIONS)),
  realtime_topic: z.string(),
});
export type CurrentOrganization = z.infer<typeof currentOrganizationSchema>;

export const propertyStatsSchema = z.object({
  active_units: z.number().int(),
  maintenance_units: z.number().int(),
  occupied_units: z.number().int(),
  arrivals_today: z.number().int(),
  departures_today: z.number().int(),
  next_arrival: z
    .object({ reservation_id: uuid, check_in: isoDateTime, guest_name: z.string(), unit_name: z.string() })
    .nullable(),
});
export type PropertyStats = z.infer<typeof propertyStatsSchema>;

export const propertySchema = z.object({
  id: uuid,
  organization_id: uuid,
  name: z.string(),
  address: z.string(),
  timezone: z.string(),
  status: z.enum(PROPERTY_STATUSES),
  default_check_in_time: z.string(),
  default_check_out_time: z.string(),
  unit_count: z.number().int(),
  stats: propertyStatsSchema.nullable().optional(),
  created_at: isoDateTime,
  updated_at: isoDateTime,
});
export type Property = z.infer<typeof propertySchema>;

export const unitSchema = z.object({
  id: uuid,
  organization_id: uuid,
  property_id: uuid,
  name: z.string(),
  type: z.enum(UNIT_TYPES),
  status: z.enum(UNIT_STATUSES),
  operational_status: z.enum(UNIT_OPERATIONAL_STATUSES).optional(),
  maintenance_blocked: z.boolean().optional(),
  created_at: isoDateTime,
  updated_at: isoDateTime,
});
export type Unit = z.infer<typeof unitSchema>;

export const guestStatsSchema = z.object({
  total_stays: z.number().int(),
  completed_stays: z.number().int(),
  last_stay_at: isoDateTime.nullable(),
  next_stay: z
    .object({
      reservation_id: uuid,
      confirmation_code: z.string(),
      status: z.enum(RESERVATION_STATUSES),
      check_in: isoDateTime,
      check_out: isoDateTime,
      property_id: uuid,
      property_name: z.string(),
      property_timezone: z.string(),
      unit_name: z.string(),
    })
    .nullable(),
  properties: z
    .array(
      z.object({
        property_id: uuid,
        property_name: z.string(),
        stays: z.number().int(),
        last_check_out: isoDateTime.nullable(),
      }),
    )
    .default([]),
});
export type GuestStats = z.infer<typeof guestStatsSchema>;

export const guestSchema = z.object({
  id: uuid,
  organization_id: uuid,
  first_name: z.string(),
  last_name: z.string(),
  email: z.string().nullable(),
  phone: z.string().nullable(),
  /** Structured stay preferences (see GuestPreferences). */
  preferences: z.record(z.string(), z.unknown()).default({}),
  /** Private team notes — staff API only, never shown to guests. */
  notes: z.string().nullable().default(null),
  version: z.number().int().default(1),
  stats: guestStatsSchema.nullable().optional(),
  created_at: isoDateTime,
  updated_at: isoDateTime,
});
export type Guest = z.infer<typeof guestSchema>;

export const GUEST_PREFERENCE_FIELDS = [
  "preferred_language",
  "contact_channel",
  "usually_requests_early_check_in",
  "usually_requests_late_checkout",
  "bedding",
  "accessibility",
  "dietary",
  "occasions",
  "other",
] as const;
export type GuestPreferences = {
  preferred_language?: string;
  contact_channel?: "IN_APP" | "EMAIL" | "SMS" | "WHATSAPP" | "PHONE";
  usually_requests_early_check_in?: boolean;
  usually_requests_late_checkout?: boolean;
  bedding?: string;
  accessibility?: string;
  dietary?: string;
  occasions?: string;
  other?: string;
};

export const reservationSchema = z.object({
  id: uuid,
  organization_id: uuid,
  confirmation_code: z.string(),
  status: z.enum(RESERVATION_STATUSES),
  check_in: isoDateTime,
  check_out: isoDateTime,
  nights: z.number().int(),
  adults: z.number().int(),
  children: z.number().int(),
  source: z.enum(RESERVATION_SOURCES),
  total_amount: money,
  currency: z.string(),
  notes: z.string().nullable(),
  version: z.number().int(),
  property: z.object({ id: uuid, name: z.string(), timezone: z.string() }),
  unit: z.object({ id: uuid, name: z.string() }),
  guest: z.object({
    id: uuid,
    first_name: z.string(),
    last_name: z.string(),
    email: z.string().nullable(),
    phone: z.string().nullable(),
  }),
  created_at: isoDateTime,
  updated_at: isoDateTime,
});
export type Reservation = z.infer<typeof reservationSchema>;

export const auditLogSchema = z.object({
  id: uuid,
  organization_id: uuid,
  actor_type: z.enum(ACTOR_TYPES),
  actor_id: uuid.nullable(),
  actor_label: z.string().nullable(),
  action: z.string(),
  entity_type: z.string(),
  entity_id: uuid,
  occurred_at: isoDateTime,
  metadata: z.record(z.string(), z.unknown()),
  request_id: z.string().nullable(),
  event_id: uuid.nullable(),
});
export type AuditLog = z.infer<typeof auditLogSchema>;

export const eventSchema = z.object({
  id: uuid,
  organization_id: uuid,
  event_type: z.string(),
  aggregate_type: z.string(),
  aggregate_id: uuid,
  occurred_at: isoDateTime,
  created_at: isoDateTime,
  payload: z.record(z.string(), z.unknown()),
  source: z.string(),
  actor_id: uuid.nullable(),
  status: z.enum(EVENT_STATUSES),
  attempts: z.number().int(),
  processed_at: isoDateTime.nullable(),
});
export type DomainEvent = z.infer<typeof eventSchema>;

export const signalSchema = z.object({
  code: z.string(),
  severity: z.enum(SIGNAL_SEVERITIES),
  title: z.string(),
  detail: z.string(),
  reservation_id: uuid.nullable().optional(),
  property_id: uuid.nullable().optional(),
  unit_id: uuid.nullable().optional(),
  task_id: uuid.nullable().optional(),
  cleaning_id: uuid.nullable().optional(),
  work_order_id: uuid.nullable().optional(),
  conversation_id: uuid.nullable().optional(),
  guest_request_id: uuid.nullable().optional(),
  alert_id: uuid.nullable().optional(),
  // In-app link for signals that aren't about one reservation (Autopilot).
  href: z.string().nullable().optional(),
});
export type OperationalSignal = z.infer<typeof signalSchema>;

export const commandCenterSchema = z.object({
  as_of: isoDateTime,
  metrics: z.object({
    check_ins_today: z.number().int(),
    check_ins_completed: z.number().int(),
    check_outs_today: z.number().int(),
    check_outs_completed: z.number().int(),
    in_house: z.number().int(),
    active_reservations: z.number().int(),
    arrivals_next_7_days: z.number().int(),
    occupancy_rate: z.number(),
    active_units: z.number().int(),
    occupied_units: z.number().int(),
    open_signals: z.number().int(),
    open_tasks: z.number().int(),
    overdue_tasks: z.number().int(),
    unassigned_tasks: z.number().int(),
    cleanings_today: z.number().int(),
    awaiting_inspection: z.number().int(),
    active_maintenance: z.number().int(),
    urgent_maintenance: z.number().int(),
    blocked_units: z.number().int(),
    unread_conversations: z.number().int().default(0),
    open_guest_requests: z.number().int().default(0),
    revenue_this_month: z.array(z.object({ currency: z.string(), amount: money })),
  }),
  check_ins: z.array(reservationSchema),
  check_outs: z.array(reservationSchema),
  in_house: z.array(reservationSchema),
  signals: z.array(signalSchema),
  recent_activity: z.array(auditLogSchema),
});
export type CommandCenter = z.infer<typeof commandCenterSchema>;

// ---------------------------------------------------------------- requests

export const guestInputSchema = z.object({
  first_name: z.string().trim().min(1, "First name is required").max(100),
  last_name: z.string().trim().min(1, "Last name is required").max(100),
  email: z.union([z.email("Enter a valid email").trim(), z.literal("")]).optional(),
  phone: z.string().trim().max(32).optional(),
});
export type GuestInput = z.infer<typeof guestInputSchema>;

export const propertyInputSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(200),
  address: z.string().trim().min(1, "Address is required").max(500),
  timezone: z.string().min(1, "Timezone is required"),
  default_check_in_time: z.string().regex(/^\d{2}:\d{2}$/),
  default_check_out_time: z.string().regex(/^\d{2}:\d{2}$/),
});
export type PropertyInput = z.infer<typeof propertyInputSchema>;

export const calendarSchema = z.object({
  start: z.iso.date(),
  end: z.iso.date(),
  properties: z.array(
    z.object({
      id: uuid,
      name: z.string(),
      timezone: z.string(),
      status: z.enum(PROPERTY_STATUSES),
      units: z.array(z.object({ id: uuid, name: z.string(), type: z.enum(UNIT_TYPES), status: z.enum(UNIT_STATUSES) })),
    }),
  ),
  reservations: z.array(reservationSchema),
});
export type CalendarData = z.infer<typeof calendarSchema>;
export type CalendarProperty = CalendarData["properties"][number];
export type CalendarUnit = CalendarProperty["units"][number];

export const availabilitySchema = z.object({
  property: z.object({ id: uuid, name: z.string(), timezone: z.string() }),
  check_in: isoDateTime,
  check_out: isoDateTime,
  units: z.array(
    z.object({
      unit_id: uuid,
      name: z.string(),
      status: z.string(),
      available: z.boolean(),
      bookable: z.boolean(),
      reason: z.string().nullable(),
      conflicts: z.array(
        z.object({
          reservation_id: uuid,
          confirmation_code: z.string(),
          guest_name: z.string(),
          status: z.enum(RESERVATION_STATUSES),
          check_in: isoDateTime,
          check_out: isoDateTime,
        }),
      ),
    }),
  ),
});
export type Availability = z.infer<typeof availabilitySchema>;
export type UnitAvailability = Availability["units"][number];
