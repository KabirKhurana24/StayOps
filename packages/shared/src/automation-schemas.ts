/**
 * Autopilot contract (Phase 6): workflows, immutable versions, executions with
 * their persisted steps, approvals, simulations and operational alerts.
 */
import { z } from "zod";

const uuid = z.uuid();
const isoDateTime = z.iso.datetime({ offset: true });
const record = z.record(z.string(), z.unknown());

export const WORKFLOW_STATUSES = ["DRAFT", "ACTIVE", "PAUSED", "ARCHIVED"] as const;
export type WorkflowStatus = (typeof WORKFLOW_STATUSES)[number];
export const EXECUTION_STATUSES = ["PENDING", "RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED"] as const;
export type ExecutionStatus = (typeof EXECUTION_STATUSES)[number];
export const EXECUTION_STEP_STATUSES = ["PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED", "WAITING_APPROVAL", "REJECTED"] as const;
export type ExecutionStepStatus = (typeof EXECUTION_STEP_STATUSES)[number];
export const APPROVAL_STATUSES = ["PENDING", "APPROVED", "EXECUTED", "REJECTED", "FAILED", "CANCELLED"] as const;
export type ApprovalStatus = (typeof APPROVAL_STATUSES)[number];
export const ALERT_SEVERITIES = ["INFO", "WARNING", "CRITICAL"] as const;
export type AlertSeverity = (typeof ALERT_SEVERITIES)[number];
export const ALERT_STATUSES = ["OPEN", "ACKNOWLEDGED", "RESOLVED"] as const;
export type AlertStatus = (typeof ALERT_STATUSES)[number];

// ------------------------------------------------------------------ definition

export const positionSchema = z.object({ x: z.number(), y: z.number() });

export type Rule = { field: string; op: string; value?: unknown };
export type RuleGroup = { match: "all" | "any"; rules: (Rule | RuleGroup)[] };

export const ruleSchema = z.object({ field: z.string(), op: z.string(), value: z.unknown().optional() });
export const ruleGroupSchema: z.ZodType<RuleGroup> = z.lazy(() =>
  z.object({ match: z.enum(["all", "any"]), rules: z.array(z.union([ruleSchema, ruleGroupSchema])) }),
);

export const triggerNodeSchema = z.object({
  id: z.string(),
  kind: z.literal("trigger"),
  type: z.string(),
  config: record,
  label: z.string().nullable().optional(),
  position: positionSchema.optional(),
});
export const conditionNodeSchema = z.object({
  id: z.string(),
  kind: z.literal("condition"),
  label: z.string().nullable().optional(),
  condition: ruleGroupSchema,
  position: positionSchema.optional(),
});
export const actionNodeSchema = z.object({
  id: z.string(),
  kind: z.literal("action"),
  type: z.string(),
  label: z.string().nullable().optional(),
  config: record,
  approval: z.enum(["auto", "required"]).nullable().optional(),
  position: positionSchema.optional(),
});
export const workflowNodeSchema = z.discriminatedUnion("kind", [triggerNodeSchema, conditionNodeSchema, actionNodeSchema]);
export type WorkflowNode = z.infer<typeof workflowNodeSchema>;
export type TriggerNode = z.infer<typeof triggerNodeSchema>;
export type ConditionNode = z.infer<typeof conditionNodeSchema>;
export type ActionNode = z.infer<typeof actionNodeSchema>;

export const BRANCHES = ["next", "true", "false"] as const;
export type Branch = (typeof BRANCHES)[number];
export const workflowEdgeSchema = z.object({
  id: z.string().nullable().optional(),
  source: z.string(),
  target: z.string(),
  branch: z.enum(BRANCHES).default("next"),
});
export type WorkflowEdge = z.infer<typeof workflowEdgeSchema>;

export const workflowDefinitionSchema = z.object({
  schema_version: z.literal(1),
  nodes: z.array(workflowNodeSchema),
  edges: z.array(workflowEdgeSchema),
  settings: z.object({ on_error: z.enum(["stop", "continue"]) }),
});
export type WorkflowDefinition = z.infer<typeof workflowDefinitionSchema>;

// --------------------------------------------------------------------- catalog

export const catalogFieldSchema = z.object({
  path: z.string(),
  label: z.string(),
  type: z.enum(["enum", "string", "number", "boolean", "id"]),
  scope: z.string(),
  values: z.array(z.string()),
  unit: z.string().nullable(),
  ops: z.array(z.string()),
});
export type CatalogField = z.infer<typeof catalogFieldSchema>;

export const catalogTriggerSchema = z.object({
  type: z.string(),
  label: z.string(),
  category: z.string(),
  kind: z.enum(["event", "schedule"]),
  subject: z.string(),
  description: z.string(),
  scopes: z.array(z.string()),
  refs: z.array(z.string()),
  config_schema: record,
});
export type CatalogTrigger = z.infer<typeof catalogTriggerSchema>;

export const catalogActionSchema = z.object({
  type: z.string(),
  label: z.string(),
  category: z.enum(["Operations", "Guest experience", "System"]),
  description: z.string(),
  risk: z.enum(["LOW", "MODERATE", "SYSTEM"]),
  guest_facing: z.boolean(),
  default_approval: z.enum(["auto", "required"]),
  always_requires_approval: z.boolean(),
  permissions: z.array(z.string()),
  template_fields: z.array(z.string()),
  member_fields: z.array(z.string()),
  input_schema: record,
});
export type CatalogAction = z.infer<typeof catalogActionSchema>;

export const automationCatalogSchema = z.object({
  triggers: z.array(catalogTriggerSchema),
  fields: z.array(catalogFieldSchema),
  operators: z.array(z.object({ op: z.string(), label: z.string() })),
  actions: z.array(catalogActionSchema),
  limits: z.object({
    max_nodes: z.number().int(),
    max_conditions: z.number().int(),
    max_actions: z.number().int(),
    max_rules_per_condition: z.number().int(),
    max_condition_depth: z.number().int(),
  }),
  alert_severities: z.array(z.enum(ALERT_SEVERITIES)),
});
export type AutomationCatalog = z.infer<typeof automationCatalogSchema>;

// ------------------------------------------------------------------- workflows

export const validationIssueSchema = z.object({
  code: z.string(),
  message: z.string(),
  node_id: z.string().nullable().optional(),
  edge_id: z.string().nullable().optional(),
  field: z.string().nullable().optional(),
});
export type ValidationIssue = z.infer<typeof validationIssueSchema>;
export const validationResultSchema = z.object({ valid: z.boolean(), issues: z.array(validationIssueSchema) });

export const workflowStatsSchema = z.object({
  executions: z.number().int(),
  succeeded: z.number().int(),
  failed: z.number().int(),
  waiting: z.number().int(),
  failed_recently: z.number().int(),
  success_rate: z.number().nullable(),
  last_execution_at: isoDateTime.nullable(),
  last_execution_status: z.enum(EXECUTION_STATUSES).nullable(),
});

export const workflowSchema = z.object({
  id: uuid,
  name: z.string(),
  description: z.string().nullable(),
  status: z.enum(WORKFLOW_STATUSES),
  trigger_type: z.string(),
  published_trigger_type: z.string().nullable(),
  current_version: z.number().int().nullable(),
  has_unpublished_changes: z.boolean(),
  template_key: z.string().nullable(),
  created_by_label: z.string().nullable(),
  updated_by_label: z.string().nullable(),
  published_at: isoDateTime.nullable(),
  created_at: isoDateTime,
  updated_at: isoDateTime,
  revision: z.number().int(),
  stats: workflowStatsSchema,
});
export type Workflow = z.infer<typeof workflowSchema>;

export const workflowDetailSchema = workflowSchema.extend({
  definition: workflowDefinitionSchema,
  issues: z.array(validationIssueSchema),
});
export type WorkflowDetail = z.infer<typeof workflowDetailSchema>;

export const workflowVersionSchema = z.object({
  id: uuid,
  version: z.number().int(),
  trigger_type: z.string(),
  checksum: z.string(),
  published_by_label: z.string().nullable(),
  published_at: isoDateTime,
  definition: workflowDefinitionSchema,
});
export type WorkflowVersion = z.infer<typeof workflowVersionSchema>;

export const workflowTemplateSchema = z.object({
  key: z.string(),
  name: z.string(),
  description: z.string(),
  trigger_type: z.string(),
  highlights: z.array(z.string()),
  definition: workflowDefinitionSchema,
});
export type WorkflowTemplate = z.infer<typeof workflowTemplateSchema>;

// ------------------------------------------------------------------ executions

export const executionStepSchema = z.object({
  id: uuid,
  seq: z.number().int(),
  step_id: z.string(),
  step_type: z.string(),
  label: z.string(),
  status: z.enum(EXECUTION_STEP_STATUSES),
  attempt: z.number().int(),
  input_summary: record,
  output_summary: record,
  error_code: z.string().nullable(),
  error_message: z.string().nullable(),
  started_at: isoDateTime.nullable(),
  completed_at: isoDateTime.nullable(),
});
export type ExecutionStep = z.infer<typeof executionStepSchema>;

export const automationApprovalSchema = z.object({
  id: uuid,
  execution_id: uuid,
  workflow_id: uuid,
  workflow_name: z.string().nullable(),
  step_id: z.string(),
  action_type: z.string(),
  risk: z.string(),
  title: z.string(),
  reason: z.string(),
  expected_effect: z.string(),
  entity_type: z.string().nullable(),
  entity_id: uuid.nullable(),
  entity_label: z.string().nullable(),
  status: z.enum(APPROVAL_STATUSES),
  decided_by_label: z.string().nullable(),
  decided_at: isoDateTime.nullable(),
  result: record,
  error: z.string().nullable(),
  version: z.number().int(),
  created_at: isoDateTime,
});
export type AutomationApproval = z.infer<typeof automationApprovalSchema>;

export const executionSchema = z.object({
  id: uuid,
  workflow_id: uuid,
  workflow_name: z.string(),
  workflow_version: z.number().int(),
  trigger_type: z.string(),
  triggering_event_id: uuid.nullable(),
  status: z.enum(EXECUTION_STATUSES),
  entity_type: z.string().nullable(),
  entity_id: uuid.nullable(),
  entity_label: z.string().nullable(),
  started_at: isoDateTime,
  completed_at: isoDateTime.nullable(),
  duration_ms: z.number().int().nullable(),
  error_summary: z.string().nullable(),
  actions_executed: z.number().int(),
  retry_count: z.number().int(),
  last_retry_at: isoDateTime.nullable(),
  correlation_id: z.string().nullable(),
});
export type Execution = z.infer<typeof executionSchema>;

export const executionDetailSchema = executionSchema.extend({
  trigger: record,
  fields: record,
  steps: z.array(executionStepSchema),
  approvals: z.array(automationApprovalSchema),
  can_retry: z.boolean(),
});
export type ExecutionDetail = z.infer<typeof executionDetailSchema>;

// ------------------------------------------------------------------ simulation

export const SIM_OUTCOMES = [
  "matched",
  "not_matched",
  "passed",
  "failed",
  "would_execute",
  "requires_approval",
  "invalid",
  "skipped",
  "would_stop",
] as const;
export type SimOutcome = (typeof SIM_OUTCOMES)[number];

export const simulationSchema = z.object({
  simulation: z.literal(true),
  valid: z.boolean(),
  issues: z.array(validationIssueSchema),
  trigger_matches: z.boolean(),
  subject: z.object({ entity_type: z.string(), entity_id: z.string(), label: z.string() }).nullable(),
  fields: record,
  steps: z.array(
    z.object({
      node_id: z.string(),
      kind: z.enum(["trigger", "condition", "action"]),
      type: z.string(),
      label: z.string(),
      outcome: z.enum(SIM_OUTCOMES),
      detail: z.string(),
      data: record,
    }),
  ),
  summary: z.string(),
});
export type Simulation = z.infer<typeof simulationSchema>;

export const simulationSampleSchema = z.object({
  kind: z.enum(["event", "entity"]),
  id: uuid,
  label: z.string(),
  occurred_at: isoDateTime.nullable().optional(),
});
export type SimulationSample = z.infer<typeof simulationSampleSchema>;

// --------------------------------------------------------------------- overview

export const automationOverviewSchema = z.object({
  active_workflows: z.number().int(),
  paused_workflows: z.number().int(),
  draft_workflows: z.number().int(),
  executions_today: z.number().int(),
  succeeded_today: z.number().int(),
  failed_today: z.number().int(),
  waiting_approval: z.number().int(),
  actions_today: z.number().int(),
  repeated_failures: z.array(z.object({ workflow_id: uuid, name: z.string(), failures: z.number().int() })),
  recent_actions: z.array(
    z.object({
      id: uuid,
      action: z.string(),
      entity_type: z.string(),
      entity_id: z.string(),
      actor_label: z.string().nullable(),
      actor_type: z.string(),
      occurred_at: isoDateTime,
      title: z.string().nullable().optional(),
      automation: record.nullable().optional(),
    }),
  ),
  recent_executions: z.array(executionSchema),
});
export type AutomationOverview = z.infer<typeof automationOverviewSchema>;

export const workflowDraftSchema = z.object({
  name: z.string(),
  description: z.string(),
  definition: workflowDefinitionSchema,
  explanation: z.array(z.string()),
  valid: z.boolean(),
  issues: z.array(validationIssueSchema),
  provider: z.string(),
  model: z.string().nullable(),
});
export type WorkflowDraft = z.infer<typeof workflowDraftSchema>;

export const operationalAlertSchema = z.object({
  id: uuid,
  severity: z.enum(ALERT_SEVERITIES),
  status: z.enum(ALERT_STATUSES),
  title: z.string(),
  detail: z.string().nullable(),
  entity_type: z.string().nullable(),
  entity_id: uuid.nullable(),
  entity_label: z.string().nullable(),
  property_id: uuid.nullable(),
  workflow_id: uuid.nullable(),
  execution_id: uuid.nullable(),
  created_by_label: z.string().nullable(),
  acknowledged_by_label: z.string().nullable(),
  acknowledged_at: isoDateTime.nullable(),
  resolved_at: isoDateTime.nullable(),
  created_at: isoDateTime,
  version: z.number().int(),
});
export type OperationalAlert = z.infer<typeof operationalAlertSchema>;
