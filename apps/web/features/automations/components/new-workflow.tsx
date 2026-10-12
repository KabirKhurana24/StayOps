"use client";

import type { WorkflowDraft } from "@stayops/shared";
import { AlertTriangle, Bot, CheckCircle2, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select, Textarea } from "@/components/ui/form";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { ApiError } from "@/lib/api/client";

import { useCatalog, useCreateWorkflow, useDraftWithAI, useTemplates, useUseTemplate } from "../api";
import { TriggerLabel } from "./parts";

export function NewWorkflowDialogs({ dialog, onClose }: { dialog: string; onClose: () => void }) {
  return (
    <>
      <BlankDialog open={dialog === "new"} onClose={onClose} />
      <TemplatesDialog open={dialog === "templates"} onClose={onClose} />
      <AIDraftDialog open={dialog === "ai"} onClose={onClose} />
    </>
  );
}

function BlankDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const catalog = useCatalog();
  const create = useCreateWorkflow();
  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState("reservation.created");
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="New workflow" description="Start with a trigger; add conditions and actions in the builder.">
      <form
        className="space-y-4 p-5"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(
            {
              name: name.trim() || "Untitled workflow",
              definition: {
                schema_version: 1,
                nodes: [{ id: "trigger", kind: "trigger", type: trigger, config: {} }],
                edges: [],
                settings: { on_error: "stop" },
              },
            },
            { onSuccess: (wf) => router.push(`/automations/${wf.id}`) },
          );
        }}
      >
        <Field label="Name" htmlFor="wf-name">
          <Input id="wf-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Overdue cleaning alert" maxLength={120} autoFocus />
        </Field>
        <Field label="Trigger" htmlFor="wf-trigger" hint="What starts this workflow. You can change it later.">
          <Select id="wf-trigger" value={trigger} onChange={(e) => setTrigger(e.target.value)}>
            {Object.entries(Object.groupBy(catalog.data?.triggers ?? [], (t) => t.category)).map(([category, triggers]) => (
              <optgroup key={category} label={category}>
                {triggers?.map((t) => (
                  <option key={t.type} value={t.type}>
                    {t.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </Select>
        </Field>
        <FormError message={create.error?.message} />
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create draft"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function TemplatesDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const templates = useTemplates();
  const catalog = useCatalog();
  const use = useUseTemplate();
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="Start from a template" description="Templates create an editable draft. Nothing runs until you publish." className="max-w-2xl">
      <div className="scrollbar-thin max-h-[65vh] overflow-y-auto">
        {templates.isPending ? (
          <SkeletonRows rows={6} />
        ) : templates.error ? (
          <ErrorState error={templates.error} compact />
        ) : (
          <ul className="divide-y" data-testid="template-list">
            {templates.data.map((t) => (
              <li key={t.key} className="flex items-start justify-between gap-4 px-5 py-3.5" data-testid="template" data-key={t.key}>
                <div className="min-w-0">
                  <div className="text-[13px] font-semibold">{t.name}</div>
                  <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{t.description}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <TriggerLabel type={t.trigger_type} catalog={catalog.data} />
                    {t.highlights.map((h) => (
                      <Badge key={h} tone="outline">
                        {h}
                      </Badge>
                    ))}
                  </div>
                </div>
                <Button
                  size="sm"
                  disabled={use.isPending}
                  aria-label={`Use template ${t.name}`}
                  onClick={() => use.mutate(t.key, { onSuccess: (wf) => router.push(`/automations/${wf.id}`) })}
                >
                  Use template
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Dialog>
  );
}

function AIDraftDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const draft = useDraftWithAI();
  const create = useCreateWorkflow();
  const [prompt, setPrompt] = useState("");
  const result: WorkflowDraft | undefined = draft.data;
  const unavailable = draft.error instanceof ApiError && draft.error.status === 503;
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          draft.reset();
          onClose();
        }
      }}
      title="Draft a workflow with AI"
      description="Describe what should happen. The AI only uses StayOps' supported triggers and actions; you review, simulate and publish."
      className="max-w-xl"
    >
      <div className="space-y-4 p-5" data-testid="ai-draft">
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (prompt.trim().length >= 5) draft.mutate(prompt.trim());
          }}
        >
          <Field label="When… then…" htmlFor="ai-prompt">
            <Textarea
              id="ai-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="When a cleaning is overdue, alert the operations team."
              maxLength={1000}
            />
          </Field>
          <div className="flex justify-end">
            <Button type="submit" variant="primary" disabled={draft.isPending || prompt.trim().length < 5}>
              <Sparkles /> {draft.isPending ? "Drafting…" : "Draft workflow"}
            </Button>
          </div>
        </form>
        {draft.error ? (
          <FormError message={draft.error.message}>
            {unavailable ? <p className="mt-1 text-xs">Manual authoring still works: use “New workflow” or a template.</p> : null}
          </FormError>
        ) : null}
        {result ? (
          <div className="rounded-lg border" data-testid="ai-draft-result">
            <div className="flex items-center justify-between gap-2 border-b px-4 py-2.5">
              <div className="flex min-w-0 items-center gap-2">
                <Bot className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                <span className="truncate text-[13px] font-semibold">{result.name}</span>
              </div>
              {result.valid ? (
                <Badge tone="success">
                  <CheckCircle2 className="size-3" /> Valid
                </Badge>
              ) : (
                <Badge tone="danger">
                  <AlertTriangle className="size-3" /> Not valid
                </Badge>
              )}
            </div>
            <ol className="space-y-1 px-4 py-3 text-[13px]">
              {result.explanation.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ol>
            {result.issues.length ? (
              <ul className="space-y-1 border-t px-4 py-3 text-xs text-danger" data-testid="ai-draft-issues">
                {result.issues.map((i, n) => (
                  <li key={`${i.code}-${n}`}>{i.message}</li>
                ))}
              </ul>
            ) : null}
            <div className="flex items-center justify-between gap-2 border-t px-4 py-2.5">
              <span className="text-xs text-muted-foreground">Proposed by {result.provider} · not saved</span>
              <Button
                size="sm"
                variant="primary"
                disabled={!result.valid || create.isPending}
                onClick={() =>
                  create.mutate(
                    { name: result.name, description: result.description || null, definition: result.definition },
                    { onSuccess: (wf) => router.push(`/automations/${wf.id}`) },
                  )
                }
              >
                Create draft
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}
