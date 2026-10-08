/**
 * Guest experience contract (Phase 4): unified inbox, guest requests, property
 * knowledge, guest portal links and the guest portal's own (guest-safe) DTOs.
 */
import { z } from "zod";

import {
  CHANNELS,
  CONVERSATION_PRIORITIES,
  CONVERSATION_STATUSES,
  GUEST_REQUEST_SOURCES,
  GUEST_REQUEST_STATUSES,
  GUEST_REQUEST_TYPES,
  KNOWLEDGE_CATEGORIES,
  KNOWLEDGE_VISIBILITIES,
  MESSAGE_TYPES,
  RESERVATION_STATUSES,
  SENDER_TYPES,
  STAY_PHASES,
  TASK_PRIORITIES,
} from "./enums";
import { guestSchema } from "./schemas";

const uuid = z.uuid();
const isoDateTime = z.iso.datetime({ offset: true });
const localTime = z.string().regex(/^\d{2}:\d{2}(:\d{2})?$/);

const propertyRef = z.object({ id: uuid, name: z.string(), timezone: z.string() });
const unitRef = z.object({ id: uuid, name: z.string() });
const memberRef = z.object({ id: uuid, name: z.string(), email: z.string().nullable().optional() });

// ------------------------------------------------------------------ inbox

export const conversationSchema = z.object({
  id: uuid,
  organization_id: uuid,
  channel: z.enum(CHANNELS),
  status: z.enum(CONVERSATION_STATUSES),
  priority: z.enum(CONVERSATION_PRIORITIES),
  subject: z.string().nullable(),
  guest: z.object({
    id: uuid,
    first_name: z.string(),
    last_name: z.string(),
    email: z.string().nullable(),
    phone: z.string().nullable(),
  }),
  reservation: z
    .object({
      id: uuid,
      confirmation_code: z.string(),
      status: z.enum(RESERVATION_STATUSES),
      check_in: isoDateTime,
      check_out: isoDateTime,
      adults: z.number().int(),
      children: z.number().int(),
      source: z.string(),
    })
    .nullable(),
  property: propertyRef.nullable(),
  unit: unitRef.nullable(),
  assignee: memberRef.nullable(),
  last_message: z
    .object({
      sender_type: z.enum(SENDER_TYPES),
      message_type: z.enum(MESSAGE_TYPES),
      content: z.string(),
      internal: z.boolean(),
      created_at: isoDateTime,
    })
    .nullable(),
  last_message_at: isoDateTime.nullable(),
  unread_count: z.number().int(),
  open_requests: z.number().int(),
  version: z.number().int(),
  created_at: isoDateTime,
  updated_at: isoDateTime,
});
export type Conversation = z.infer<typeof conversationSchema>;

export const inboxCountsSchema = z.object({
  all: z.number().int(),
  unread: z.number().int(),
  open: z.number().int(),
  mine: z.number().int(),
  high: z.number().int(),
  resolved: z.number().int(),
});
export type InboxCounts = z.infer<typeof inboxCountsSchema>;

export const messageSchema = z.object({
  id: uuid,
  conversation_id: uuid,
  seq: z.number().int(),
  sender_type: z.enum(SENDER_TYPES),
  sender_id: uuid.nullable(),
  sender_label: z.string().nullable(),
  message_type: z.enum(MESSAGE_TYPES),
  content: z.string(),
  internal: z.boolean(),
  metadata: z.record(z.string(), z.unknown()),
  created_at: isoDateTime,
  read_at: isoDateTime.nullable(),
});
export type Message = z.infer<typeof messageSchema>;

export const channelInfoSchema = z.object({
  channel: z.enum(CHANNELS),
  label: z.string(),
  status: z.enum(["CONNECTED", "NOT_CONFIGURED"]),
});
export type ChannelInfo = z.infer<typeof channelInfoSchema>;

// ------------------------------------------------------------------ guest requests

const linkedWork = z.object({ id: uuid, title: z.string(), status: z.string() });

export const guestRequestSchema = z.object({
  id: uuid,
  organization_id: uuid,
  conversation_id: uuid,
  reservation_id: uuid,
  confirmation_code: z.string(),
  guest_id: uuid,
  guest_name: z.string(),
  property: propertyRef,
  unit: unitRef,
  type: z.enum(GUEST_REQUEST_TYPES),
  priority: z.enum(TASK_PRIORITIES),
  status: z.enum(GUEST_REQUEST_STATUSES),
  source: z.enum(GUEST_REQUEST_SOURCES),
  description: z.string(),
  requested_for: isoDateTime.nullable(),
  assignee: memberRef.nullable(),
  task: linkedWork.nullable(),
  work_order: linkedWork.nullable(),
  resolution_note: z.string().nullable(),
  created_by_label: z.string().nullable(),
  created_at: isoDateTime,
  updated_at: isoDateTime,
  resolved_at: isoDateTime.nullable(),
  closed_at: isoDateTime.nullable(),
  version: z.number().int(),
});
export type GuestRequest = z.infer<typeof guestRequestSchema>;

export const guestRequestCountsSchema = z.object({
  open: z.number().int(),
  in_progress: z.number().int(),
  resolved: z.number().int(),
  all: z.number().int(),
});
export type GuestRequestCounts = z.infer<typeof guestRequestCountsSchema>;

export const GUEST_REQUEST_LABEL: Record<(typeof GUEST_REQUEST_TYPES)[number], string> = {
  EARLY_CHECK_IN: "Early check-in",
  LATE_CHECKOUT: "Late checkout",
  EXTRA_TOWELS: "Extra towels",
  HOUSEKEEPING: "Housekeeping",
  MAINTENANCE: "Maintenance issue",
  QUESTION: "Question",
  OTHER: "Other request",
};

// ------------------------------------------------------------------ context panel

export const portalLinkStatusSchema = z.object({
  active: z.boolean(),
  created_at: isoDateTime.nullable().optional(),
  expires_at: isoDateTime.nullable().optional(),
  last_used_at: isoDateTime.nullable().optional(),
  created_by_label: z.string().nullable().optional(),
});
export type PortalLinkStatus = z.infer<typeof portalLinkStatusSchema>;

export const portalLinkIssuedSchema = z.object({
  token: z.string(),
  path: z.string(),
  expires_at: isoDateTime,
});
export type PortalLinkIssued = z.infer<typeof portalLinkIssuedSchema>;

export const conversationContextSchema = z.object({
  conversation: conversationSchema,
  guest: guestSchema,
  reservation: z
    .object({
      id: uuid,
      confirmation_code: z.string(),
      status: z.enum(RESERVATION_STATUSES),
      check_in: isoDateTime,
      check_out: isoDateTime,
      adults: z.number().int(),
      children: z.number().int(),
      source: z.string(),
      total_amount: z.union([z.string(), z.number()]).transform(String),
      currency: z.string(),
      notes: z.string().nullable(),
      phase: z.enum(STAY_PHASES),
    })
    .nullable(),
  property: z
    .object({
      id: uuid,
      name: z.string(),
      address: z.string(),
      timezone: z.string(),
      check_in_time: localTime,
      check_out_time: localTime,
    })
    .nullable(),
  unit: z
    .object({
      id: uuid,
      name: z.string(),
      type: z.string(),
      operational_status: z.string(),
      maintenance_blocked: z.boolean(),
    })
    .nullable(),
  requests: z.array(guestRequestSchema),
  work: z.array(
    z.object({
      id: uuid,
      kind: z.enum(["task", "work_order"]),
      title: z.string(),
      status: z.string(),
      priority: z.string(),
      due_at: isoDateTime.nullable().optional(),
      assignee: z.string().nullable().optional(),
      blocks_unit: z.boolean().optional(),
    }),
  ),
  portal_link: portalLinkStatusSchema.nullable(),
});
export type ConversationContext = z.infer<typeof conversationContextSchema>;

// ------------------------------------------------------------------ knowledge

export const knowledgeEntrySchema = z.object({
  id: uuid,
  property_id: uuid,
  unit_id: uuid.nullable(),
  unit_name: z.string().nullable(),
  category: z.enum(KNOWLEDGE_CATEGORIES),
  title: z.string(),
  content: z.string(),
  visibility: z.enum(KNOWLEDGE_VISIBILITIES),
  sort_order: z.number().int(),
  updated_by_label: z.string().nullable(),
  version: z.number().int(),
  created_at: isoDateTime,
  updated_at: isoDateTime,
});
export type KnowledgeEntry = z.infer<typeof knowledgeEntrySchema>;

export const KNOWLEDGE_CATEGORY_LABEL: Record<(typeof KNOWLEDGE_CATEGORIES)[number], string> = {
  WIFI: "Wi-Fi",
  CHECK_IN: "Check-in",
  CHECK_OUT: "Check-out",
  HOUSE_RULES: "House rules",
  PARKING: "Parking",
  DIRECTIONS: "Directions",
  AMENITIES: "Amenities",
  APPLIANCES: "Appliances",
  EMERGENCY: "Emergency",
  LOCAL_INFO: "Local tips",
  OTHER: "Other",
};

// ------------------------------------------------------------------ guest portal (guest-safe)

export const guestStaySchema = z.object({
  host_name: z.string(),
  guest_first_name: z.string(),
  guest_name: z.string(),
  reservation: z.object({
    confirmation_code: z.string(),
    status: z.enum(RESERVATION_STATUSES),
    check_in: isoDateTime,
    check_out: isoDateTime,
    nights: z.number().int(),
    adults: z.number().int(),
    children: z.number().int(),
  }),
  property: z.object({
    name: z.string(),
    address: z.string(),
    timezone: z.string(),
    check_in_time: localTime,
    check_out_time: localTime,
  }),
  unit_name: z.string(),
  phase: z.enum(STAY_PHASES),
  stay_info_available_from: isoDateTime,
  stay_info_available: z.boolean(),
  unread_messages: z.number().int(),
  open_requests: z.number().int(),
});
export type GuestStay = z.infer<typeof guestStaySchema>;

export const guestKnowledgeSchema = z.object({
  sections: z.array(
    z.object({
      category: z.enum(KNOWLEDGE_CATEGORIES),
      entries: z.array(z.object({ id: uuid, title: z.string(), content: z.string() })),
    }),
  ),
  stay_info_available: z.boolean(),
  stay_info_available_from: isoDateTime,
});
export type GuestKnowledge = z.infer<typeof guestKnowledgeSchema>;

export const guestMessageSchema = z.object({
  id: uuid,
  seq: z.number().int(),
  sender: z.enum(["guest", "host", "assistant", "system"]),
  sender_label: z.string(),
  message_type: z.enum(MESSAGE_TYPES),
  content: z.string(),
  created_at: isoDateTime,
  read_at: isoDateTime.nullable(),
  request_id: uuid.nullable().optional(),
});
export type GuestMessage = z.infer<typeof guestMessageSchema>;

export const guestRequestViewSchema = z.object({
  id: uuid,
  type: z.enum(GUEST_REQUEST_TYPES),
  label: z.string(),
  status: z.enum(GUEST_REQUEST_STATUSES),
  description: z.string(),
  requested_for: isoDateTime.nullable(),
  created_at: isoDateTime,
  resolved_at: isoDateTime.nullable(),
  can_cancel: z.boolean(),
});
export type GuestRequestView = z.infer<typeof guestRequestViewSchema>;
