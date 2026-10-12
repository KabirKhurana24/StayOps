"use client";

import type {
  ActionNode,
  AutomationCatalog,
  ConditionNode,
  ValidationIssue,
  WorkflowDefinition,
  WorkflowDetail,
  WorkflowNode,
} from "@stayops/shared";
import { Archive, ArrowLeft, CheckCircle2, FlaskConical, GitBranch, History, Pause, Play, Rocket, Save, SearchX, ShieldCheck, Zap } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Input, Select } from "@/components/ui/form";
import { useBreadcrumb } from "@/components/shell/shell-context";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { ApiError } from "@/lib/api/client";
import { formatRelative } from "@/lib/format";
import { useSearchState } from "@/lib/use-search-state";

import { useCatalog, useSaveWorkflow, useValidate, useVersions, useWorkflow, useWorkflowAction } from "../api";
import { ExecutionSheet, ExecutionsTab } from "../components/executions";
import { WorkflowStatusBadge } from "../components/parts";
import { connect, insertNode, newNodeId, nodeById, removeNode, sameDefinition, type Slot, triggerOf, updateNode } from "../graph";
import { nodeTitle, WorkflowCanvas, WorkflowOutline } from "./canvas";
import { NodeConfig } from "./node-config";
import { SimulationPanel } from "./simulation-panel";

export function WorkflowEditor({ id }: { id: string }) {
  const workflow = useWorkflow(id);
  const catalog = useCatalog();
  useBreadcrumb(workflow.data?.name ?? null);
  if (workflow.isPending || catalog.isPending) {
    return (
      <div className="space-y-3" aria-busy="true" aria-label="Loading workflow">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-[520px] w-full" />
      </div>
    );
  }
  if (workflow.error) {
    return workflow.error instanceof ApiError && workflow.error.status === 404 ? (
      <EmptyState icon={SearchX} title="Workflow not found" description="It may have been removed, or it belongs to another organization." />
    ) : (
      <ErrorState error={workflow.error} onRetry={() => workflow.refetch()} />
    );
  }
  if (catalog.error) return <ErrorState error={catalog.error} onRetry={() => catalog.refetch()} />;
  // Re-mount the editing state when another revision arrives from the server.
  return <Editor key={`${workflow.data.id}`} workflow={workflow.data} catalog={catalog.data} />;
}

function Editor({ workflow, catalog }: { workflow: WorkflowDetail; catalog: AutomationCatalog }) {
  const router = useRouter();
  const org = useCurrentOrganization();
  const canEdit = can(org.data, "automations:manage") && workflow.status !== "ARCHIVED";
  const { get, set } = useSearchState();
  const save = useSaveWorkflow(workflow.id);
  const act = useWorkflowAction(workflow.id);
  const validate = useValidate(workflow.id);

  const [definition, setDefinition] = useState<WorkflowDefinition>(workflow.definition);
  const [name, setName] = useState(workflow.name);
  const [baseline, setBaseline] = useState({ definition: workflow.definition, name: workflow.name, revision: workflow.revision });
  const [selected, setSelected] = useState<string | null>(triggerOf(workflow.definition)?.id ?? null);
  const [slot, setSlot] = useState<Slot | null>(null);
  const [issues, setIssues] = useState<ValidationIssue[]>(workflow.issues);
  const [notice, setNotice] = useState<string | null>(null);
  const [leaveTo, setLeaveTo] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<{ id: string; count: number } | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);

  // A newer revision from someone else (realtime) — adopt it if we have no edits.
  const dirty = !sameDefinition(definition, baseline.definition) || name !== baseline.name;
  const [prevServer, setPrevServer] = useState(workflow);
  if (workflow !== prevServer) {
    setPrevServer(workflow);
    if (!dirty && workflow.revision !== baseline.revision) {
      setDefinition(workflow.definition);
      setName(workflow.name);
      setBaseline({ definition: workflow.definition, name: workflow.name, revision: workflow.revision });
      setIssues(workflow.issues);
    }
  }

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const trigger = triggerOf(definition);
  const selectedNode = selected ? nodeById(definition, selected) : undefined;
  const globalIssues = issues.filter((i) => !i.node_id || !nodeById(definition, i.node_id));

  const persist = async (): Promise<WorkflowDetail | null> => {
    try {
      const saved = await save.mutateAsync({ name: name.trim() || "Untitled workflow", definition, expected_revision: baseline.revision });
      setBaseline({ definition: saved.definition, name: saved.name, revision: saved.revision });
      setDefinition(saved.definition);
      setIssues(saved.issues);
      return saved;
    } catch {
      return null;
    }
  };

  const onSave = async () => {
    const saved = await persist();
    if (saved) setNotice(saved.issues.length ? `Draft saved with ${saved.issues.length} problem(s) to fix before publishing.` : "Draft saved.");
  };

  const onValidate = async () => {
    const result = await validate.mutateAsync(definition);
    setIssues(result.issues);
    setNotice(result.valid ? "No problems found — ready to publish." : `${result.issues.length} problem(s) to fix.`);
  };

  const onPublish = async () => {
    let revision = baseline.revision;
    if (dirty) {
      const saved = await persist();
      if (!saved) return;
      revision = saved.revision;
    }
    try {
      const published = await act.mutateAsync({ action: "publish", revision });
      setBaseline({ definition: published.definition, name: published.name, revision: published.revision });
      setIssues(published.issues);
      setNotice(`Published version ${published.current_version}. It runs on new matching events from now on.`);
    } catch (err) {
      if (err instanceof ApiError && Array.isArray(err.details)) setIssues(err.details as ValidationIssue[]);
    }
  };

  const onStatus = async (action: "pause" | "resume") => {
    try {
      const wf = await act.mutateAsync({ action, revision: dirty ? undefined : baseline.revision });
      setBaseline((b) => ({ ...b, revision: wf.revision }));
      setNotice(action === "pause" ? "Paused — new events won't start this workflow." : "Resumed.");
    } catch {
      /* error shown below */
    }
  };

  const addStep = (kind: "condition" | "action", type?: string) => {
    if (!canEdit) return;
    const target: Slot | null = slot ?? (selected ? { source: selected, branch: nodeById(definition, selected)?.kind === "condition" ? "true" : "next" } : trigger ? { source: trigger.id, branch: "next" } : null);
    if (!target) return;
    const node: ActionNode | ConditionNode =
      kind === "condition"
        ? { id: newNodeId(definition, "condition"), kind: "condition", label: null, condition: { match: "all", rules: [] } }
        : { id: newNodeId(definition, type ?? "action"), kind: "action", type: type ?? "", config: {}, label: null, approval: null };
    try {
      setDefinition(insertNode(definition, target, node));
      setSelected(node.id);
      setSlot(null);
    } catch (err) {
      setNotice((err as Error).message);
    }
  };

  const doRemove = (nodeId: string) => {
    const { definition: next } = removeNode(definition, nodeId);
    setDefinition(next);
    setSelected(trigger?.id ?? null);
    setConfirmRemove(null);
  };

  const requestRemove = (nodeId: string) => {
    const { removed } = removeNode(definition, nodeId);
    if (removed.length > 1) setConfirmRemove({ id: nodeId, count: removed.length });
    else doRemove(nodeId);
  };

  const navigate = (href: string) => (dirty ? setLeaveTo(href) : router.push(href));
  const pending = save.isPending || act.isPending;
  const error = save.error ?? act.error;

  return (
    <div data-testid="workflow-editor" data-dirty={dirty} data-status={workflow.status}>
      <button type="button" onClick={() => navigate("/automations?tab=workflows")} className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> All workflows
      </button>

      {/* Toolbar */}
      <div className="mb-3 flex flex-col gap-2 rounded-lg border bg-surface px-3 py-2.5 shadow-xs xl:flex-row xl:items-center xl:justify-between">
        <div className="flex min-w-0 items-center gap-2">
          <Input aria-label="Workflow name" value={name} maxLength={120} disabled={!canEdit} onChange={(e) => setName(e.target.value)} className="h-8 w-full max-w-sm font-semibold" />
          <WorkflowStatusBadge status={workflow.status} />
          {workflow.current_version ? <Badge tone="outline">v{workflow.current_version}</Badge> : null}
          {dirty ? (
            <Badge tone="warning" data-testid="unsaved">
              Unsaved changes
            </Badge>
          ) : workflow.has_unpublished_changes && workflow.current_version ? (
            <Badge tone="outline">Draft differs from v{workflow.current_version}</Badge>
          ) : null}
        </div>
        {canEdit ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <Button size="sm" onClick={onSave} disabled={pending || !dirty}>
              <Save /> {save.isPending ? "Saving…" : "Save draft"}
            </Button>
            <Button size="sm" onClick={onValidate} disabled={validate.isPending}>
              <CheckCircle2 /> Validate
            </Button>
            <Button size="sm" onClick={() => set({ simulate: "1" }, { push: true })}>
              <FlaskConical /> Simulate
            </Button>
            {workflow.status === "ACTIVE" ? (
              <Button size="sm" onClick={() => onStatus("pause")} disabled={pending}>
                <Pause /> Pause
              </Button>
            ) : workflow.status === "PAUSED" ? (
              <Button size="sm" onClick={() => onStatus("resume")} disabled={pending}>
                <Play /> Resume
              </Button>
            ) : null}
            <Button size="sm" variant="ghost" onClick={() => setConfirmArchive(true)} disabled={pending}>
              <Archive /> Archive
            </Button>
            <Button size="sm" variant="primary" onClick={onPublish} disabled={pending}>
              <Rocket /> {act.isPending ? "Working…" : "Publish"}
            </Button>
          </div>
        ) : (
          <Badge tone="outline">Read only</Badge>
        )}
      </div>

      {notice || error || globalIssues.length ? (
        <div className="mb-3 space-y-2" aria-live="polite">
          {notice ? (
            <div className="flex items-center justify-between rounded-md border border-info/20 bg-info/5 px-3 py-2 text-[13px] text-info" data-testid="editor-notice">
              <span>{notice}</span>
              <button type="button" className="text-xs underline" onClick={() => setNotice(null)}>
                Dismiss
              </button>
            </div>
          ) : null}
          {error ? (
            <div role="alert" className="rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-[13px] text-danger">
              {error.message}
              {error instanceof ApiError && error.isConflict ? " Reload the page to get the latest version." : null}
            </div>
          ) : null}
          {globalIssues.length ? (
            <ul className="space-y-0.5 rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-xs text-danger" data-testid="workflow-issues">
              {globalIssues.map((i, n) => (
                <li key={`${i.code}-${n}`}>{i.message}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {/* Phones / narrow tablets: readable stacked view. */}
      <div className="lg:hidden">
        <p className="mb-2 text-xs text-muted-foreground">Viewing mode — open on a larger screen to edit the workflow graph.</p>
        <WorkflowOutline definition={definition} catalog={catalog} />
      </div>

      <div className="hidden gap-3 lg:grid lg:grid-cols-[220px_minmax(0,1fr)_340px]">
        <NodeLibrary catalog={catalog} disabled={!canEdit} slot={slot} definition={definition} onAdd={addStep} />
        <div className="h-[calc(100vh-15rem)] min-h-[480px] overflow-hidden rounded-lg border bg-surface-raised shadow-xs">
          <WorkflowCanvas
            definition={definition}
            catalog={catalog}
            issues={issues}
            selected={selected}
            slot={slot}
            readOnly={!canEdit}
            onSelect={(nodeId) => {
              setSelected(nodeId);
              setSlot(null);
            }}
            onSlot={(s) => {
              setSlot(s);
              setSelected(s.source);
            }}
          />
        </div>
        <aside className="scrollbar-thin h-[calc(100vh-15rem)] min-h-[480px] overflow-y-auto rounded-lg border bg-surface p-4 shadow-xs" aria-label="Step settings">
          {selectedNode ? (
            <>
              <h2 className="mb-3 text-[13px] font-semibold">{nodeTitle(selectedNode, catalog)}</h2>
              <NodeConfig
                node={selectedNode}
                definition={definition}
                catalog={catalog}
                issues={issues}
                readOnly={!canEdit}
                onChange={(node: WorkflowNode) => setDefinition(updateNode(definition, node.id, () => node))}
                onRemove={() => requestRemove(selectedNode.id)}
                onConnect={(s) => {
                  try {
                    setDefinition(connect(definition, s, selectedNode.id));
                  } catch (err) {
                    setNotice((err as Error).message);
                  }
                }}
              />
            </>
          ) : null}
          <WorkflowSettings workflowId={workflow.id} definition={definition} readOnly={!canEdit} onChange={setDefinition} />
        </aside>
      </div>

      <section className="mt-6">
        <h2 className="mb-2 text-[13px] font-semibold">Runs of this workflow</h2>
        <ExecutionsTab workflowId={workflow.id} onOpen={(eid) => set({ e: eid }, { push: true })} />
      </section>

      <ExecutionSheet id={get("e") || null} onClose={() => set({ e: null })} />
      <SimulationPanel
        open={get("simulate") === "1"}
        onOpenChange={(o) => set({ simulate: o ? "1" : null })}
        workflowId={workflow.id}
        definition={definition}
        triggerType={trigger?.type ?? workflow.trigger_type}
      />
      <ConfirmDialog
        open={leaveTo !== null}
        onOpenChange={(o) => !o && setLeaveTo(null)}
        title="Discard unsaved changes?"
        description="Your edits to this workflow haven't been saved."
        confirmLabel="Discard changes"
        cancelLabel="Keep editing"
        onConfirm={() => {
          const href = leaveTo;
          setLeaveTo(null);
          setBaseline((b) => ({ ...b, definition, name }));
          if (href) router.push(href);
        }}
      />
      <ConfirmDialog
        open={confirmArchive}
        onOpenChange={setConfirmArchive}
        title="Archive this workflow?"
        description="It stops running immediately; queued runs are cancelled and pending approvals withdrawn. Its history stays available."
        confirmLabel="Archive workflow"
        pending={act.isPending}
        onConfirm={async () => {
          try {
            await act.mutateAsync({ action: "archive" });
            setBaseline((b) => ({ ...b, definition, name }));
            setConfirmArchive(false);
            router.push("/automations?tab=workflows");
          } catch {
            setConfirmArchive(false);
          }
        }}
      />
      <ConfirmDialog
        open={confirmRemove !== null}
        onOpenChange={(o) => !o && setConfirmRemove(null)}
        title="Remove this condition?"
        description={`This also removes ${confirmRemove ? confirmRemove.count - 1 : 0} step(s) on its “No” branch. Steps on “Yes” stay connected.`}
        confirmLabel="Remove"
        onConfirm={() => confirmRemove && doRemove(confirmRemove.id)}
      />
    </div>
  );
}

function NodeLibrary({
  catalog,
  disabled,
  slot,
  definition,
  onAdd,
}: {
  catalog: AutomationCatalog;
  disabled: boolean;
  slot: Slot | null;
  definition: WorkflowDefinition;
  onAdd: (kind: "condition" | "action", type?: string) => void;
}) {
  const groups = useMemo(() => Object.entries(Object.groupBy(catalog.actions, (a) => a.category)), [catalog.actions]);
  const target = slot ? nodeById(definition, slot.source) : undefined;
  return (
    <aside className="scrollbar-thin h-[calc(100vh-15rem)] min-h-[480px] overflow-y-auto rounded-lg border bg-surface p-3 shadow-xs" aria-label="Step library" data-testid="node-library">
      <p className="mb-3 text-xs text-muted-foreground">
        {target
          ? `Adding after “${nodeTitle(target, catalog)}”${slot?.branch === "true" ? " (Yes)" : slot?.branch === "false" ? " (No)" : ""}.`
          : "Pick a + on the canvas, or select a step, then choose what comes next."}
      </p>
      <h3 className="eyebrow mb-1.5">Triggers</h3>
      <p className="mb-3 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Zap className="size-3.5" /> Change the trigger in its settings.
      </p>
      <h3 className="eyebrow mb-1.5">Conditions</h3>
      <button type="button" disabled={disabled} onClick={() => onAdd("condition")} className="mb-3 flex w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-left text-[13px] hover:bg-muted/60 disabled:opacity-50" aria-label="Add Condition">
        <GitBranch className="size-3.5 text-muted-foreground" /> Condition (yes / no)
      </button>
      {groups.map(([category, actions]) => (
        <div key={category} className="mb-3">
          <h3 className="eyebrow mb-1.5">{category}</h3>
          <div className="space-y-1">
            {actions?.map((a) => (
              <button
                key={a.type}
                type="button"
                disabled={disabled}
                onClick={() => onAdd("action", a.type)}
                aria-label={`Add ${a.label}`}
                title={a.description}
                className="flex w-full items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-left text-[13px] hover:bg-muted/60 disabled:opacity-50"
              >
                <span className="truncate">{a.label}</span>
                {a.risk === "MODERATE" || a.always_requires_approval ? <ShieldCheck className="size-3.5 shrink-0 text-warning" aria-label="Approval by default" /> : null}
              </button>
            ))}
          </div>
        </div>
      ))}
    </aside>
  );
}

function WorkflowSettings({
  workflowId,
  definition,
  readOnly,
  onChange,
}: {
  workflowId: string;
  definition: WorkflowDefinition;
  readOnly: boolean;
  onChange: (d: WorkflowDefinition) => void;
}) {
  const versions = useVersions(workflowId);
  return (
    <div className="mt-6 space-y-4 border-t pt-4">
      <div>
        <h3 className="eyebrow mb-1.5">If a step fails</h3>
        <Select
          aria-label="If a step fails"
          value={definition.settings.on_error}
          disabled={readOnly}
          onChange={(e) => onChange({ ...definition, settings: { on_error: e.target.value as "stop" | "continue" } })}
        >
          <option value="stop">Stop the run (retry later)</option>
          <option value="continue">Record it and continue</option>
        </Select>
      </div>
      <div>
        <h3 className="eyebrow mb-1.5 flex items-center gap-1.5">
          <History className="size-3.5" /> Published versions
        </h3>
        {versions.data?.length ? (
          <ul className="space-y-1 text-xs" data-testid="version-history">
            {versions.data.map((v) => (
              <li key={v.id} className="flex items-center justify-between gap-2">
                <span className="font-medium">v{v.version}</span>
                <span className="truncate text-muted-foreground">
                  {v.published_by_label ?? "—"} · {formatRelative(v.published_at)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">Not published yet. Publishing freezes the draft into an immutable version.</p>
        )}
      </div>
      <Link href="/automations?tab=runs" className="block text-xs text-accent hover:underline">
        All execution history
      </Link>
    </div>
  );
}
