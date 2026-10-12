"use client";

import { Bot, Loader2 } from "lucide-react";

import { useAIRuns } from "@/features/ai/api";
import { RunStatusBadge } from "@/features/ai/components/run-parts";
import { visibleSteps } from "@/features/ai/model";

/**
 * The guest assistant's latest run in this thread: live while it works (steps arrive
 * over realtime as they're committed), then a one-line outcome.
 */
export function AssistantActivity({ conversationId }: { conversationId: string }) {
  const runs = useAIRuns({ conversation_id: conversationId, limit: 1 });
  const run = runs.data?.[0];
  if (!run) return null;
  const steps = visibleSteps(run.steps).filter((s) => s.kind !== "CONTEXT");
  const current = steps.findLast((s) => s.status === "STARTED") ?? steps.at(-1);
  if (run.status !== "RUNNING" && run.status !== "ESCALATED") return null;
  return (
    <div className="flex items-center gap-2 border-t bg-accent-soft/40 px-4 py-1.5 text-xs" data-testid="assistant-activity" data-status={run.status}>
      {run.status === "RUNNING" ? <Loader2 className="size-3.5 animate-spin text-accent" /> : <Bot className="size-3.5 text-info" />}
      <span className="min-w-0 flex-1 truncate">
        {run.status === "RUNNING" ? `Assistant: ${current?.label ?? "working"}…` : "The assistant handed this conversation to the team."}
      </span>
      <RunStatusBadge run={run} />
    </div>
  );
}
