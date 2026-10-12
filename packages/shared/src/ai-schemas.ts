/**
 * AI contract (Phase 5): agent runs, their live steps, and proposals awaiting
 * human approval. Steps are persisted rows — the UI renders exactly what happened.
 */
import { z } from "zod";

import { AGENT_TYPES, PROPOSAL_STATUSES, RUN_STATUSES, STEP_KINDS, STEP_STATUSES } from "./enums";

const uuid = z.uuid();
const isoDateTime = z.iso.datetime({ offset: true });

export const aiStatusSchema = z.object({
  enabled: z.boolean(),
  provider: z.string(),
  model: z.string().nullable(),
});
export type AIStatus = z.infer<typeof aiStatusSchema>;

export const aiStepSchema = z.object({
  id: uuid,
  seq: z.number().int(),
  kind: z.enum(STEP_KINDS),
  status: z.enum(STEP_STATUSES),
  label: z.string(),
  tool_name: z.string().nullable(),
  input_summary: z.record(z.string(), z.unknown()),
  result_summary: z.record(z.string(), z.unknown()),
  duration_ms: z.number().int().nullable(),
  created_at: isoDateTime,
});
export type AIStep = z.infer<typeof aiStepSchema>;

export const aiProposalSchema = z.object({
  id: uuid,
  run_id: uuid,
  tool_name: z.string(),
  title: z.string(),
  reason: z.string(),
  entity_type: z.string().nullable(),
  entity_id: uuid.nullable(),
  entity_label: z.string().nullable(),
  expected_effect: z.string(),
  status: z.enum(PROPOSAL_STATUSES),
  decided_by_label: z.string().nullable(),
  decided_at: isoDateTime.nullable(),
  result: z.record(z.string(), z.unknown()),
  error: z.string().nullable(),
  version: z.number().int(),
  created_at: isoDateTime,
});
export type AIProposal = z.infer<typeof aiProposalSchema>;

export const aiEvidenceSchema = z.object({ type: z.string(), id: z.string(), label: z.string() });
export type AIEvidence = z.infer<typeof aiEvidenceSchema>;

export const aiRunSchema = z.object({
  id: uuid,
  agent_type: z.enum(AGENT_TYPES),
  status: z.enum(RUN_STATUSES),
  question: z.string().nullable(),
  answer: z.string().nullable(),
  evidence: z.array(aiEvidenceSchema),
  conversation_id: uuid.nullable(),
  reservation_id: uuid.nullable(),
  provider: z.string(),
  model: z.string().nullable(),
  input_tokens: z.number().int(),
  output_tokens: z.number().int(),
  estimated_cost_usd: z.union([z.string(), z.number()]).transform(Number),
  error_code: z.string().nullable(),
  started_at: isoDateTime,
  completed_at: isoDateTime.nullable(),
  steps: z.array(aiStepSchema),
  proposals: z.array(aiProposalSchema),
});
export type AIRun = z.infer<typeof aiRunSchema>;

export const guestAssistantSchema = z.object({
  enabled: z.boolean(),
  running: z.boolean(),
  steps: z.array(z.object({ label: z.string(), status: z.enum(STEP_STATUSES) })),
  status: z.enum(RUN_STATUSES).nullable(),
});
export type GuestAssistant = z.infer<typeof guestAssistantSchema>;
