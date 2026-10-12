"use client";

import type { AIProposal, AIRun, AIStep } from "@stayops/shared";
import { Check, CircleAlert, CircleSlash, Loader2, ShieldCheck, Sparkles, ThumbsDown, ThumbsUp } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

import { useDecideProposal } from "../api";
import { evidenceHref, formatCost, PROPOSAL_STATUS_LABEL, RUN_STATUS_LABEL, visibleSteps } from "../model";

function StepIcon({ step }: { step: AIStep }) {
  if (step.status === "STARTED") return <Loader2 className="size-3.5 animate-spin text-accent" aria-label="In progress" />;
  if (step.status === "FAILED") return <CircleAlert className="size-3.5 text-danger" aria-label="Failed" />;
  if (step.status === "BLOCKED") return <ShieldCheck className="size-3.5 text-warning" aria-label="Guardrail" />;
  return <Check className="size-3.5 text-success" aria-label="Done" />;
}

/** The run's real, persisted steps — streamed in by realtime as they're committed. */
export function RunTimeline({ run }: { run: AIRun }) {
  const steps = visibleSteps(run.steps);
  return (
    <ol className="space-y-1.5" aria-label="What the AI did" data-testid="ai-steps">
      {steps.map((s) => (
        <li key={s.id} className="flex items-center gap-2 text-xs" data-testid="ai-step" data-status={s.status} data-tool={s.tool_name ?? undefined}>
          <StepIcon step={s} />
          <span className={cn(s.status === "STARTED" ? "text-foreground" : "text-muted-foreground")}>{s.label}</span>
          {s.duration_ms !== null && s.status !== "STARTED" ? <span className="text-[10px] text-subtle-foreground tabular">{s.duration_ms} ms</span> : null}
          {typeof s.result_summary.error === "string" ? <span className="text-[10px] text-danger">({String(s.result_summary.error).replaceAll("_", " ")})</span> : null}
        </li>
      ))}
      {run.status === "RUNNING" && !steps.some((s) => s.status === "STARTED") ? (
        <li className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" /> Working…
        </li>
      ) : null}
    </ol>
  );
}

export function RunStatusBadge({ run }: { run: AIRun }) {
  const tone = run.status === "COMPLETED" ? "success" : run.status === "AWAITING_APPROVAL" ? "warning" : run.status === "FAILED" ? "danger" : run.status === "ESCALATED" ? "info" : "accent";
  return (
    <Badge tone={tone} dot data-testid="ai-run-status" data-status={run.status}>
      {RUN_STATUS_LABEL[run.status]}
    </Badge>
  );
}

export function AnswerCard({ run }: { run: AIRun }) {
  if (!run.answer) return null;
  return (
    <div className="space-y-3" data-testid="ai-answer">
      <p className="text-[13px] leading-relaxed whitespace-pre-line">{run.answer}</p>
      {run.evidence.length ? (
        <div>
          <p className="mb-1 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">Based on</p>
          <ul className="flex flex-wrap gap-1.5" aria-label="Evidence" data-testid="ai-evidence">
            {run.evidence.map((e) => {
              const href = evidenceHref(e);
              const chip = (
                <span className="inline-flex max-w-[260px] items-center gap-1 truncate rounded-md border bg-surface px-1.5 py-0.5 text-[11px]">
                  <span className="text-subtle-foreground">{e.type.replace("_", " ")}</span> {e.label}
                </span>
              );
              return <li key={`${e.type}-${e.id}`}>{href ? <Link href={href} className="hover:opacity-80">{chip}</Link> : chip}</li>;
            })}
          </ul>
        </div>
      ) : null}
      <p className="text-[10px] text-subtle-foreground">
        {run.provider === "mock" ? "Deterministic test provider" : `${run.provider}${run.model ? ` · ${run.model}` : ""}`} ·{" "}
        {run.input_tokens + run.output_tokens} tokens · cost {formatCost(run.estimated_cost_usd)}
      </p>
    </div>
  );
}

export function ProposalCard({ p, canDecide }: { p: AIProposal; canDecide: boolean }) {
  const decide = useDecideProposal(p.id);
  const href = p.entity_type && p.entity_id ? evidenceHref({ type: p.entity_type, id: p.entity_id, label: p.entity_label ?? "" }) : null;
  return (
    <div className="rounded-lg border border-warning/30 bg-warning/5 p-3" data-testid="ai-proposal" data-status={p.status}>
      <div className="flex items-start justify-between gap-2">
        <p className="flex items-center gap-1.5 text-[13px] font-medium">
          <Sparkles className="size-3.5 text-accent" /> {p.title}
        </p>
        <Badge tone={p.status === "EXECUTED" ? "success" : p.status === "FAILED" ? "danger" : p.status === "REJECTED" ? "neutral" : "warning"}>
          {PROPOSAL_STATUS_LABEL[p.status]}
        </Badge>
      </div>
      <dl className="mt-2 grid grid-cols-[90px_1fr] gap-x-2 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Why</dt>
        <dd>{p.reason}</dd>
        <dt className="text-muted-foreground">Affects</dt>
        <dd>{href ? <Link href={href} className="text-accent hover:underline">{p.entity_label}</Link> : (p.entity_label ?? "—")}</dd>
        <dt className="text-muted-foreground">Effect</dt>
        <dd>{p.expected_effect}</dd>
      </dl>
      {p.status === "FAILED" && p.error ? <p className="mt-2 text-xs text-danger">Not done: {p.error}</p> : null}
      {p.decided_by_label && p.status !== "PENDING" ? <p className="mt-1.5 text-[11px] text-muted-foreground">Decided by {p.decided_by_label}</p> : null}
      {p.status === "PENDING" && canDecide ? (
        <div className="mt-3 flex gap-2">
          <Button size="sm" variant="primary" disabled={decide.isPending} onClick={() => decide.mutate({ approve: true, version: p.version })}>
            <ThumbsUp /> Approve
          </Button>
          <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate({ approve: false, version: p.version })}>
            <ThumbsDown /> Reject
          </Button>
        </div>
      ) : null}
      {decide.error ? <p className="mt-1.5 text-xs text-danger">{decide.error instanceof ApiError ? decide.error.message : "Couldn't record the decision."}</p> : null}
    </div>
  );
}

export function NotConfigured() {
  return (
    <div className="flex items-start gap-2 rounded-lg border bg-muted/40 p-3 text-xs text-muted-foreground" data-testid="ai-not-configured">
      <CircleSlash className="mt-0.5 size-3.5 shrink-0" />
      <span>
        AI isn&apos;t configured for this workspace. Set <code>AI_PROVIDER=openai</code> with <code>OPENAI_API_KEY</code> (or <code>mock</code> for a
        deterministic demo) on the API.
      </span>
    </div>
  );
}
