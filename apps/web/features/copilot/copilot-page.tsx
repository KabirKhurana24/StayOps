"use client";

import { Bot, History, Send } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/form";
import { PageHeader, Panel } from "@/components/ui/panel";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { useAIRun, useAIRuns, useAIStatus, useAskCopilot } from "@/features/ai/api";
import { AnswerCard, NotConfigured, ProposalCard, RunStatusBadge, RunTimeline } from "@/features/ai/components/run-parts";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { ApiError } from "@/lib/api/client";
import { formatRelative } from "@/lib/format";
import { useSearchState } from "@/lib/use-search-state";
import { cn } from "@/lib/utils";

export const COPILOT_SUGGESTIONS = [
  "What needs my attention today?",
  "Any overdue tasks?",
  "Which units are blocked?",
  "Show active maintenance",
  "Which guests are waiting for a reply?",
];

/** Approving runs the action as you: offer it only if you hold the permission it needs. */
function canApprove(org: ReturnType<typeof useCurrentOrganization>["data"], tool: string): boolean {
  return tool === "create_maintenance" ? can(org, "maintenance:manage") : can(org, "tasks:manage");
}

function CurrentRun({ runId }: { runId: string }) {
  const org = useCurrentOrganization();
  const run = useAIRun(runId);
  if (run.isPending) return <Skeleton className="h-40 w-full" />;
  if (run.error) return <ErrorState error={run.error} onRetry={() => run.refetch()} />;
  const r = run.data;
  return (
    <Panel
      title={r.question ?? "Copilot"}
      icon={<Bot />}
      actions={<RunStatusBadge run={r} />}
      bodyClassName="grid gap-5 p-4 lg:grid-cols-[minmax(0,1fr)_260px]"
    >
      <div className="min-w-0 space-y-4" data-testid="copilot-run" data-status={r.status}>
        {r.status === "RUNNING" ? <p className="text-[13px] text-muted-foreground">Looking at your live operations…</p> : <AnswerCard run={r} />}
        {r.proposals.length ? (
          <div className="space-y-2">
            <p className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">Proposed actions</p>
            {r.proposals.map((p) => (
              <ProposalCard key={p.id} p={p} canDecide={canApprove(org.data, p.tool_name)} />
            ))}
          </div>
        ) : null}
      </div>
      <div className="border-t pt-3 lg:border-t-0 lg:border-l lg:pt-0 lg:pl-4">
        <p className="mb-2 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">Steps</p>
        <RunTimeline run={r} />
      </div>
    </Panel>
  );
}

export function CopilotPage() {
  const { get, set } = useSearchState();
  const status = useAIStatus();
  const ask = useAskCopilot();
  const history = useAIRuns({ limit: 15 });
  const [question, setQuestion] = useState("");
  const runId = get("run") || null;
  const asked = useRef<string | null>(null);

  const submit = (q: string) => {
    const text = q.trim();
    if (!text || ask.isPending) return;
    ask.mutate(text, {
      onSuccess: (run) => {
        setQuestion("");
        set({ run: run.id, q: null }, { push: true });
      },
    });
  };
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit(question);
  };

  // "Ask Copilot" from the Command Center arrives as ?q=… — ask exactly once.
  const fromLink = get("q");
  const { mutate: askNow } = ask;
  useEffect(() => {
    if (fromLink && status.data?.enabled && asked.current !== fromLink) {
      asked.current = fromLink;
      askNow(fromLink, { onSuccess: (run) => set({ run: run.id, q: null }) });
    }
  }, [fromLink, status.data?.enabled, askNow, set]);

  const enabled = status.data?.enabled ?? false;
  return (
    <div data-testid="copilot-page">
      <PageHeader title="Copilot" description="Ask about today's operations. Answers come from your live data; changes need your approval." />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-5">
          {status.isPending ? null : !enabled ? (
            <NotConfigured />
          ) : (
            <form onSubmit={onSubmit} className="rounded-lg border bg-surface p-3 shadow-xs" aria-label="Ask Copilot">
              <div className="flex items-end gap-2">
                <Textarea
                  aria-label="Question"
                  rows={2}
                  maxLength={500}
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      submit(question);
                    }
                  }}
                  placeholder="What needs my attention today?"
                  className="min-h-[52px] resize-none"
                />
                <Button type="submit" variant="primary" disabled={!question.trim() || ask.isPending}>
                  <Send /> Ask
                </Button>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {COPILOT_SUGGESTIONS.map((s) => (
                  <button key={s} type="button" onClick={() => submit(s)} disabled={ask.isPending} className="rounded-full border px-2.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted">
                    {s}
                  </button>
                ))}
              </div>
              {ask.error ? <p className="mt-2 text-xs text-danger">{ask.error instanceof ApiError ? ask.error.message : "Couldn't ask Copilot."}</p> : null}
            </form>
          )}
          {runId ? (
            <CurrentRun key={runId} runId={runId} />
          ) : enabled ? (
            <EmptyState icon={Bot} title="Ask your first question" description="Copilot reads arrivals, departures, tasks, maintenance, turnovers, guest requests and your inbox — then suggests what to do." />
          ) : null}
        </div>
        <Panel title="Recent" icon={<History />} bodyClassName="max-h-[560px] overflow-y-auto">
          {history.data?.length ? (
            <ul className="divide-y" aria-label="Recent Copilot questions">
              {history.data.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => set({ run: r.id }, { push: true })}
                    className={cn("w-full px-3 py-2.5 text-left hover:bg-muted/50", r.id === runId && "bg-accent-soft/60")}
                  >
                    <span className="block truncate text-[13px]">{r.question}</span>
                    <span className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                      <RunStatusBadge run={r} /> {formatRelative(r.started_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="p-4 text-xs text-muted-foreground">Your questions appear here.</p>
          )}
        </Panel>
      </div>
    </div>
  );
}
