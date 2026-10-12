import { z } from "zod";

/** Private per-organization broadcast topic (see migration 0002). */
export const organizationTopic = (organizationId: string) => `org:${organizationId}`;

export const BROADCAST_TABLES = [
  "reservations",
  "properties",
  "units",
  "guests",
  "events",
  "audit_logs",
  "tasks",
  "cleanings",
  "maintenance_work_orders",
  "conversations",
  "messages",
  "guest_requests",
  "property_knowledge",
  "ai_runs",
  "ai_run_steps",
  "ai_action_proposals",
  "automation_workflows",
  "automation_workflow_versions",
  "automation_executions",
  "automation_step_executions",
  "automation_approvals",
  "operational_alerts",
] as const;
export type BroadcastTable = (typeof BROADCAST_TABLES)[number];

/**
 * Minimal change notice emitted by `stayops_private.broadcast_change()`.
 * Contains ids and routing hints only — clients refetch through the API.
 */
export const broadcastPayloadSchema = z.object({
  table: z.enum(BROADCAST_TABLES),
  operation: z.enum(["INSERT", "UPDATE", "DELETE"]),
  id: z.string(),
  organization_id: z.string(),
  status: z.string().optional(),
  event_type: z.string().optional(),
  action: z.string().optional(),
  entity_type: z.string().optional(),
  entity_id: z.string().optional(),
  aggregate_id: z.string().optional(),
  conversation_id: z.string().optional(),
  run_id: z.string().optional(),
  workflow_id: z.string().optional(),
  execution_id: z.string().optional(),
  at: z.string().optional(),
});
export type BroadcastPayload = z.infer<typeof broadcastPayloadSchema>;
