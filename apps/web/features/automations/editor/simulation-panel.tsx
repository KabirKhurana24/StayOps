"use client";

import type { WorkflowDefinition } from "@stayops/shared";
import { FlaskConical } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FormError, Select } from "@/components/ui/form";
import { Sheet } from "@/components/ui/sheet";
import { formatClock } from "@/lib/format";

import { useSamples, useSimulate } from "../api";
import { SIM_OUTCOME } from "../model";

export function SimulationPanel({
  open,
  onOpenChange,
  workflowId,
  definition,
  triggerType,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflowId: string;
  definition: WorkflowDefinition;
  triggerType: string;
}) {
  const samples = useSamples(workflowId, triggerType, { enabled: open });
  const simulate = useSimulate(workflowId);
  const [picked, setPicked] = useState("");
  const sample = samples.data?.find((s) => s.id === picked) ?? samples.data?.[0];
  const result = simulate.data;
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Simulate" description="Dry run against real data — nothing is created, sent or changed." size="lg">
      <div className="space-y-4" data-testid="simulation-panel">
        <div className="flex items-start gap-2 rounded-md border border-info/20 bg-info/5 px-3 py-2 text-xs text-info">
          <FlaskConical className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>Simulation uses your unsaved edits and a real event or record. It never creates tasks, sends messages, blocks units or changes reservations.</span>
        </div>
        <form
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            if (!sample) return;
            simulate.mutate({ definition, ...(sample.kind === "event" ? { event_id: sample.id } : { entity_id: sample.id }) });
          }}
        >
          <Field label="Sample" htmlFor="sim-sample" className="flex-1" hint={samples.data && samples.data.length === 0 ? "No recent events of this type yet — create one, then simulate." : undefined}>
            <Select id="sim-sample" value={sample?.id ?? ""} onChange={(e) => setPicked(e.target.value)} disabled={!samples.data?.length}>
              {(samples.data ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                  {s.occurred_at ? ` · ${formatClock(s.occurred_at)}` : ""}
                </option>
              ))}
            </Select>
          </Field>
          <Button type="submit" variant="primary" disabled={!sample || simulate.isPending}>
            {simulate.isPending ? "Simulating…" : "Run simulation"}
          </Button>
        </form>
        <FormError message={simulate.error?.message} />
        {result ? (
          <section data-testid="simulation-result" data-matches={result.trigger_matches}>
            <div className="mb-2 flex items-center gap-2">
              <Badge tone="info">Simulation</Badge>
              <p className="text-[13px] font-medium">{result.summary}</p>
            </div>
            {result.issues.length ? (
              <ul className="mb-3 space-y-1 rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-xs text-danger">
                {result.issues.map((i, n) => (
                  <li key={`${i.code}-${n}`}>{i.message}</li>
                ))}
              </ul>
            ) : null}
            <ol className="divide-y rounded-lg border">
              {result.steps.map((s) => {
                const o = SIM_OUTCOME[s.outcome];
                return (
                  <li key={s.node_id} className="px-3 py-2.5" data-testid="simulation-step" data-node-id={s.node_id} data-outcome={s.outcome}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-[13px] font-medium">{s.label}</span>
                      <Badge tone={o.tone}>{o.label}</Badge>
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">{s.detail}</p>
                    {s.data.expected_effect ? <p className="mt-0.5 text-xs text-subtle-foreground">Effect: {String(s.data.expected_effect)}</p> : null}
                  </li>
                );
              })}
            </ol>
          </section>
        ) : null}
      </div>
    </Sheet>
  );
}
