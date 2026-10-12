/** Pure AI presentation logic (unit-tested): labels, evidence links, step visibility. */
import type { AIEvidence, AIStep, ProposalStatus, RunStatus } from "@stayops/shared";

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  RUNNING: "Working…",
  COMPLETED: "Done",
  AWAITING_APPROVAL: "Needs your approval",
  ESCALATED: "Handed to the team",
  FAILED: "Couldn't finish",
};

export const PROPOSAL_STATUS_LABEL: Record<ProposalStatus, string> = {
  PENDING: "Awaiting approval",
  APPROVED: "Approved",
  EXECUTED: "Approved · done",
  REJECTED: "Rejected",
  FAILED: "Approved · couldn't be done",
};

/** Where an evidence item opens in the app (null when it has no screen). */
export function evidenceHref(e: AIEvidence): string | null {
  switch (e.type) {
    case "task":
      return `/tasks?t=${e.id}`;
    case "work_order":
      return `/maintenance?wo=${e.id}`;
    case "reservation":
      return `/reservations/${e.id}`;
    case "conversation":
      return `/inbox?c=${e.id}`;
    case "guest":
      return `/guests/${e.id}`;
    case "property":
      return `/properties/${e.id}`;
    default:
      return null;
  }
}

/** Steps a person cares about: context, tools, proposals, guardrails, the answer — not raw model turns. */
export function visibleSteps(steps: AIStep[]): AIStep[] {
  return steps.filter((s) => s.kind !== "LLM" || s.status === "FAILED");
}

export const isLive = (status: RunStatus) => status === "RUNNING";

/** "$0.0012" for providers that report usage, "—" when there is no cost. */
export function formatCost(usd: number): string {
  if (!usd) return "—";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}
