"use client";

import type {
  ActionNode,
  AutomationCatalog,
  CatalogField,
  ConditionNode,
  Rule,
  RuleGroup,
  TriggerNode,
  ValidationIssue,
  WorkflowDefinition,
  WorkflowNode,
} from "@stayops/shared";
import { Braces, Plus, Trash2, X } from "lucide-react";
import { useId, useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, Label, Select, Textarea } from "@/components/ui/form";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { Segmented } from "@/components/ui/segmented";
import { useStaff } from "@/features/operations/api";
import { useProperties } from "@/features/properties/api";

import { canConnect, effectiveApproval, isRuleGroup, nodeById, parentOf, placeholderFields } from "../graph";
import { RiskBadge } from "../components/parts";
import { ENUM_LABEL, type FormField, schemaFields } from "./schema-fields";

export type NodeConfigProps = {
  node: WorkflowNode;
  definition: WorkflowDefinition;
  catalog: AutomationCatalog;
  issues: ValidationIssue[];
  readOnly: boolean;
  onChange: (node: WorkflowNode) => void;
  onRemove: () => void;
  onConnect: (slot: { source: string; branch: "next" | "true" | "false" }) => void;
};

export function NodeConfig(props: NodeConfigProps) {
  const { node, issues } = props;
  const own = issues.filter((i) => i.node_id === node.id);
  return (
    <div className="space-y-4" data-testid="node-config" data-node-id={node.id}>
      {own.length ? (
        <ul className="space-y-1 rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-xs text-danger" data-testid="node-issues">
          {own.map((i, n) => (
            <li key={`${i.code}-${n}`}>{i.message}</li>
          ))}
        </ul>
      ) : null}
      {node.kind === "trigger" ? <TriggerConfig {...props} node={node} /> : null}
      {node.kind === "condition" ? <ConditionConfig {...props} node={node} /> : null}
      {node.kind === "action" ? <ActionConfig {...props} node={node} /> : null}
      {node.kind !== "trigger" ? <Connection {...props} /> : null}
    </div>
  );
}

// ------------------------------------------------------------------- trigger

function TriggerConfig({ node, catalog, readOnly, onChange }: NodeConfigProps & { node: TriggerNode }) {
  const trigger = catalog.triggers.find((t) => t.type === node.type);
  const fields = useMemo(() => schemaFields(trigger?.config_schema ?? {}), [trigger]);
  const id = useId();
  return (
    <>
      <Field label="Trigger" htmlFor={`${id}-type`} hint={trigger?.description}>
        <Select
          id={`${id}-type`}
          value={node.type}
          disabled={readOnly}
          onChange={(e) => onChange({ ...node, type: e.target.value, config: {} })}
        >
          {Object.entries(Object.groupBy(catalog.triggers, (t) => t.category)).map(([category, triggers]) => (
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
      <SchemaInputs fields={fields} values={node.config} readOnly={readOnly} onChange={(config) => onChange({ ...node, config })} />
    </>
  );
}

// ----------------------------------------------------------------- condition

function scopedFields(catalog: AutomationCatalog, definition: WorkflowDefinition): CatalogField[] {
  const trig = definition.nodes.find((n): n is TriggerNode => n.kind === "trigger");
  const scopes = catalog.triggers.find((t) => t.type === trig?.type)?.scopes ?? [];
  return placeholderFields(catalog.fields, scopes);
}

function ConditionConfig({ node, catalog, definition, readOnly, onChange }: NodeConfigProps & { node: ConditionNode }) {
  const fields = scopedFields(catalog, definition);
  const id = useId();
  return (
    <>
      <Field label="Label" htmlFor={`${id}-label`}>
        <Input
          id={`${id}-label`}
          value={node.label ?? ""}
          maxLength={120}
          disabled={readOnly}
          placeholder="e.g. Same-day turnover"
          onChange={(e) => onChange({ ...node, label: e.target.value || null })}
        />
      </Field>
      <GroupEditor
        group={node.condition}
        fields={fields}
        depth={1}
        readOnly={readOnly}
        onChange={(condition) => onChange({ ...node, condition })}
      />
      <p className="text-xs text-subtle-foreground">“Yes” continues when the condition is met; “No” otherwise.</p>
    </>
  );
}

function GroupEditor({
  group,
  fields,
  depth,
  readOnly,
  onChange,
  onRemove,
}: {
  group: RuleGroup;
  fields: CatalogField[];
  depth: number;
  readOnly: boolean;
  onChange: (g: RuleGroup) => void;
  onRemove?: () => void;
}) {
  const setRule = (i: number, rule: Rule | RuleGroup) => onChange({ ...group, rules: group.rules.map((r, n) => (n === i ? rule : r)) });
  const removeRule = (i: number) => onChange({ ...group, rules: group.rules.filter((_, n) => n !== i) });
  const first = fields[0];
  const newRule = (): Rule => ({ field: first?.path ?? "", op: first?.ops[0] ?? "eq", value: first ? defaultValue(first, first.ops[0]) : null });
  return (
    <div className={depth > 1 ? "space-y-2 rounded-md border border-dashed p-2.5" : "space-y-2"} data-testid="rule-group">
      <div className="flex items-center justify-between gap-2">
        <Segmented
          label="Match"
          size="sm"
          value={group.match}
          onChange={(match) => !readOnly && onChange({ ...group, match })}
          options={[
            { value: "all", label: "All of" },
            { value: "any", label: "Any of" },
          ]}
        />
        {onRemove && !readOnly ? (
          <Button size="icon-sm" variant="ghost" aria-label="Remove group" onClick={onRemove}>
            <X />
          </Button>
        ) : null}
      </div>
      {group.rules.map((rule, i) =>
        isRuleGroup(rule) ? (
          <GroupEditor
            key={i}
            group={rule}
            fields={fields}
            depth={depth + 1}
            readOnly={readOnly}
            onChange={(g) => setRule(i, g)}
            onRemove={() => removeRule(i)}
          />
        ) : (
          <RuleEditor key={i} rule={rule} fields={fields} readOnly={readOnly} onChange={(r) => setRule(i, r)} onRemove={() => removeRule(i)} />
        ),
      )}
      {!readOnly ? (
        <div className="flex gap-1.5">
          <Button size="xs" onClick={() => onChange({ ...group, rules: [...group.rules, newRule()] })}>
            <Plus /> Rule
          </Button>
          {depth < 2 ? (
            <Button size="xs" variant="ghost" onClick={() => onChange({ ...group, rules: [...group.rules, { match: "any", rules: [newRule()] }] })}>
              <Plus /> Group
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function defaultValue(field: CatalogField, op: string): unknown {
  if (op === "exists" || op === "not_exists") return null;
  if (op === "in" || op === "not_in") return field.values.slice(0, 1);
  if (field.type === "boolean") return true;
  if (field.type === "number") return 0;
  if (field.type === "enum") return field.values[0] ?? "";
  return "";
}

function RuleEditor({
  rule,
  fields,
  readOnly,
  onChange,
  onRemove,
}: {
  rule: Rule;
  fields: CatalogField[];
  readOnly: boolean;
  onChange: (r: Rule) => void;
  onRemove: () => void;
}) {
  const field = fields.find((f) => f.path === rule.field);
  const id = useId();
  const groups = Object.groupBy(fields, (f) => f.scope);
  const ops = field?.ops ?? ["eq"];
  const noValue = rule.op === "exists" || rule.op === "not_exists";
  return (
    <div className="space-y-1.5 rounded-md border bg-surface-raised p-2" data-testid="rule">
      <div className="flex gap-1.5">
        <Select
          aria-label="Field"
          wrapperClassName="flex-1"
          value={rule.field}
          disabled={readOnly}
          onChange={(e) => {
            const next = fields.find((f) => f.path === e.target.value);
            const op = next?.ops.includes(rule.op) ? rule.op : (next?.ops[0] ?? "eq");
            onChange({ field: e.target.value, op, value: next ? defaultValue(next, op) : null });
          }}
        >
          {field ? null : <option value={rule.field}>{rule.field || "Choose a field"}</option>}
          {Object.entries(groups).map(([scope, list]) => (
            <optgroup key={scope} label={SCOPE_LABEL[scope] ?? scope}>
              {list?.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.label}
                </option>
              ))}
            </optgroup>
          ))}
        </Select>
        {!readOnly ? (
          <Button size="icon-sm" variant="ghost" aria-label="Remove rule" onClick={onRemove}>
            <X />
          </Button>
        ) : null}
      </div>
      <div className="flex gap-1.5">
        <Select
          aria-label="Operator"
          wrapperClassName="w-32 shrink-0"
          value={rule.op}
          disabled={readOnly}
          onChange={(e) => onChange({ ...rule, op: e.target.value, value: field ? defaultValue(field, e.target.value) : null })}
        >
          {ops.map((op) => (
            <option key={op} value={op}>
              {OPS[op] ?? op}
            </option>
          ))}
        </Select>
        {noValue ? null : field?.type === "boolean" ? (
          <Select aria-label="Value" wrapperClassName="flex-1" value={String(rule.value)} disabled={readOnly} onChange={(e) => onChange({ ...rule, value: e.target.value === "true" })}>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </Select>
        ) : field?.type === "enum" && (rule.op === "in" || rule.op === "not_in") ? (
          <MultiEnum id={id} values={field.values} selected={(rule.value as string[]) ?? []} readOnly={readOnly} onChange={(value) => onChange({ ...rule, value })} />
        ) : field?.type === "enum" ? (
          <Select aria-label="Value" wrapperClassName="flex-1" value={String(rule.value ?? "")} disabled={readOnly} onChange={(e) => onChange({ ...rule, value: e.target.value })}>
            {field.values.map((v) => (
              <option key={v} value={v}>
                {ENUM_LABEL(v)}
              </option>
            ))}
          </Select>
        ) : field?.type === "number" ? (
          <Input
            aria-label="Value"
            type="number"
            className="flex-1"
            value={String(rule.value ?? 0)}
            disabled={readOnly}
            onChange={(e) => onChange({ ...rule, value: e.target.value === "" ? 0 : Number(e.target.value) })}
          />
        ) : (
          <Input
            aria-label="Value"
            className="flex-1"
            value={Array.isArray(rule.value) ? rule.value.join(", ") : String(rule.value ?? "")}
            disabled={readOnly}
            onChange={(e) =>
              onChange({
                ...rule,
                value: rule.op === "in" || rule.op === "not_in" ? e.target.value.split(",").map((v) => v.trim()).filter(Boolean) : e.target.value,
              })
            }
          />
        )}
      </div>
      {field?.unit && !noValue ? <p className="text-[11px] text-subtle-foreground">in {field.unit}</p> : null}
    </div>
  );
}

const SCOPE_LABEL: Record<string, string> = {
  event: "Event",
  property: "Property",
  unit: "Unit",
  reservation: "Reservation",
  turnover: "Turnover",
  task: "Task",
  cleaning: "Cleaning",
  work_order: "Work order",
  guest_request: "Guest request",
  conversation: "Conversation",
  message: "Message",
  arrival: "Next arrival",
  summary: "Daily summary",
};

const OPS: Record<string, string> = {
  eq: "is",
  neq: "is not",
  gt: "greater than",
  gte: "at least",
  lt: "less than",
  lte: "at most",
  in: "is one of",
  not_in: "is none of",
  exists: "is set",
  not_exists: "is not set",
};

function MultiEnum({ id, values, selected, readOnly, onChange }: { id: string; values: readonly string[]; selected: string[]; readOnly: boolean; onChange: (v: string[]) => void }) {
  return (
    <div className="flex flex-1 flex-wrap gap-1" role="group" aria-label="Values">
      {values.map((v) => {
        const on = selected.includes(v);
        return (
          <label key={v} htmlFor={`${id}-${v}`} className={on ? "cursor-pointer rounded-[5px] border border-accent/30 bg-accent-soft px-1.5 py-0.5 text-[11px] text-accent" : "cursor-pointer rounded-[5px] border px-1.5 py-0.5 text-[11px] text-muted-foreground"}>
            <input
              id={`${id}-${v}`}
              type="checkbox"
              className="sr-only"
              checked={on}
              disabled={readOnly}
              onChange={() => onChange(on ? selected.filter((s) => s !== v) : [...selected, v])}
            />
            {ENUM_LABEL(v)}
          </label>
        );
      })}
    </div>
  );
}

// -------------------------------------------------------------------- action

function ActionConfig({ node, catalog, definition, readOnly, onChange, onRemove }: NodeConfigProps & { node: ActionNode }) {
  const action = catalog.actions.find((a) => a.type === node.type);
  const fields = useMemo(() => schemaFields(action?.input_schema ?? {}), [action]);
  const placeholders = scopedFields(catalog, definition);
  const policy = effectiveApproval(node, action);
  const id = useId();
  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[13px] font-semibold">{action?.label ?? node.type}</span>
        {action ? <RiskBadge risk={action.risk} /> : <Badge tone="danger">Unsupported</Badge>}
        {action?.guest_facing ? <Badge tone="info">Guest-facing</Badge> : null}
      </div>
      {action ? <p className="text-xs text-muted-foreground">{action.description}</p> : null}
      <Field label="Step label" htmlFor={`${id}-label`}>
        <Input id={`${id}-label`} value={node.label ?? ""} maxLength={120} disabled={readOnly} placeholder={action?.label} onChange={(e) => onChange({ ...node, label: e.target.value || null })} />
      </Field>
      <SchemaInputs
        fields={fields}
        values={node.config}
        readOnly={readOnly}
        templateFields={action?.template_fields ?? []}
        placeholders={placeholders}
        onChange={(config) => onChange({ ...node, config })}
      />
      <Field
        label="Approval"
        htmlFor={`${id}-approval`}
        hint={
          action?.always_requires_approval
            ? "This step always waits for a person."
            : policy === "required"
              ? "The workflow pauses here until someone approves."
              : "Runs automatically."
        }
      >
        <Select
          id={`${id}-approval`}
          value={node.approval ?? "default"}
          disabled={readOnly || action?.always_requires_approval}
          onChange={(e) => onChange({ ...node, approval: e.target.value === "default" ? null : (e.target.value as "auto" | "required") })}
        >
          <option value="default">Default ({action?.default_approval === "required" ? "ask first" : "automatic"})</option>
          <option value="required">Always ask for approval</option>
          <option value="auto">Run automatically</option>
        </Select>
      </Field>
      {!readOnly ? (
        <Button size="sm" variant="danger" onClick={onRemove}>
          <Trash2 /> Remove step
        </Button>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------- inputs

function SchemaInputs({
  fields,
  values,
  readOnly,
  onChange,
  templateFields = [],
  placeholders = [],
}: {
  fields: FormField[];
  values: Record<string, unknown>;
  readOnly: boolean;
  onChange: (values: Record<string, unknown>) => void;
  templateFields?: string[];
  placeholders?: CatalogField[];
}) {
  const id = useId();
  const staff = useStaff({ enabled: fields.some((f) => f.kind === "member") });
  const properties = useProperties();
  const set = (name: string, value: unknown) => {
    const next = { ...values };
    if (value === undefined || value === "" || value === null) delete next[name];
    else next[name] = value;
    onChange(next);
  };
  return (
    <div className="space-y-3">
      {fields.map((f) => {
        const fid = `${id}-${f.name}`;
        const value = values[f.name] ?? f.default;
        const label = `${f.title}${f.required ? "" : " (optional)"}`;
        const insert = templateFields.includes(f.name) && !readOnly ? (
          <PlaceholderMenu fields={placeholders} onPick={(path) => set(f.name, `${String(values[f.name] ?? "")}{{${path}}}`)} />
        ) : null;
        if (f.kind === "textarea" || f.kind === "text") {
          return (
            <div key={f.name} className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor={fid}>{label}</Label>
                {insert}
              </div>
              {f.kind === "textarea" ? (
                <Textarea id={fid} value={String(values[f.name] ?? "")} maxLength={f.maxLength} disabled={readOnly} onChange={(e) => set(f.name, e.target.value)} />
              ) : (
                <Input id={fid} value={String(values[f.name] ?? "")} maxLength={f.maxLength} disabled={readOnly} onChange={(e) => set(f.name, e.target.value)} />
              )}
            </div>
          );
        }
        if (f.kind === "number") {
          return (
            <Field key={f.name} label={label} htmlFor={fid}>
              <Input id={fid} type="number" min={f.min} max={f.max} value={value === undefined ? "" : String(value)} disabled={readOnly} onChange={(e) => set(f.name, e.target.value === "" ? undefined : Number(e.target.value))} />
            </Field>
          );
        }
        if (f.kind === "time") {
          return (
            <Field key={f.name} label={label} htmlFor={fid} hint="In each property's own timezone.">
              <Input id={fid} type="time" value={String(value ?? "")} disabled={readOnly} onChange={(e) => set(f.name, e.target.value)} />
            </Field>
          );
        }
        if (f.kind === "boolean") {
          return (
            <label key={f.name} htmlFor={fid} className="flex items-center gap-2 text-[13px]">
              <input id={fid} type="checkbox" checked={Boolean(value)} disabled={readOnly} onChange={(e) => set(f.name, e.target.checked)} />
              {f.title}
            </label>
          );
        }
        if (f.kind === "enum") {
          return (
            <Field key={f.name} label={label} htmlFor={fid}>
              <Select id={fid} value={String(value ?? "")} disabled={readOnly} onChange={(e) => set(f.name, e.target.value)}>
                {f.required || value !== undefined ? null : <option value="">—</option>}
                {f.options.map((o) => (
                  <option key={o} value={o}>
                    {ENUM_LABEL(o)}
                  </option>
                ))}
              </Select>
            </Field>
          );
        }
        if (f.kind === "enum_multi") {
          return (
            <div key={f.name} className="flex flex-col gap-1.5">
              <Label>{label}</Label>
              <MultiEnum id={fid} values={f.options} selected={(value as string[]) ?? []} readOnly={readOnly} onChange={(v) => set(f.name, v)} />
            </div>
          );
        }
        if (f.kind === "member") {
          return (
            <Field key={f.name} label={label} htmlFor={fid}>
              <Select id={fid} value={String(value ?? "")} disabled={readOnly} onChange={(e) => set(f.name, e.target.value || undefined)}>
                <option value="">{f.required ? "Choose a team member" : "Nobody"}</option>
                {(staff.data ?? []).map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.name}
                  </option>
                ))}
              </Select>
            </Field>
          );
        }
        if (f.kind === "property") {
          return (
            <Field key={f.name} label={label} htmlFor={fid}>
              <Select id={fid} value={String(value ?? "")} disabled={readOnly} onChange={(e) => set(f.name, e.target.value || undefined)}>
                <option value="">Choose a property</option>
                {(properties.data?.items ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
          );
        }
        // property_multi
        const selected = (value as string[]) ?? [];
        return (
          <div key={f.name} className="flex flex-col gap-1.5">
            <Label>Properties (empty = all)</Label>
            <div className="flex flex-wrap gap-1">
              {(properties.data?.items ?? []).map((p) => {
                const on = selected.includes(p.id);
                return (
                  <label key={p.id} className={on ? "cursor-pointer rounded-[5px] border border-accent/30 bg-accent-soft px-1.5 py-0.5 text-[11px] text-accent" : "cursor-pointer rounded-[5px] border px-1.5 py-0.5 text-[11px] text-muted-foreground"}>
                    <input type="checkbox" className="sr-only" checked={on} disabled={readOnly} onChange={() => set(f.name, on ? selected.filter((s) => s !== p.id) : [...selected, p.id])} />
                    {p.name}
                  </label>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PlaceholderMenu({ fields, onPick }: { fields: CatalogField[]; onPick: (path: string) => void }) {
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button size="xs" variant="ghost" aria-label="Insert a value">
          <Braces /> Insert value
        </Button>
      </MenuTrigger>
      <MenuContent className="max-h-72 overflow-y-auto">
        {fields
          .filter((f) => f.type !== "id")
          .map((f) => (
            <MenuItem key={f.path} onSelect={() => onPick(f.path)}>
              <span className="text-[13px]">{f.label}</span>
              <span className="ml-auto pl-3 font-mono text-[10px] text-subtle-foreground">{f.path}</span>
            </MenuItem>
          ))}
      </MenuContent>
    </Menu>
  );
}

// ---------------------------------------------------------------- connection

function Connection({ node, definition, readOnly, onConnect }: NodeConfigProps) {
  const parent = parentOf(definition, node.id);
  const id = useId();
  if (parent) {
    const source = nodeById(definition, parent.source);
    const label = source?.kind === "trigger" ? "the trigger" : (source?.label ?? source?.id);
    return (
      <p className="border-t pt-3 text-xs text-muted-foreground">
        Runs after <span className="font-medium text-foreground">{label}</span>
        {parent.branch === "true" ? " (Yes)" : parent.branch === "false" ? " (No)" : ""}.
      </p>
    );
  }
  const slots = definition.nodes.flatMap((n) =>
    (n.kind === "condition" ? (["true", "false"] as const) : (["next"] as const)).map((branch) => ({ source: n.id, branch, label: `${n.kind === "trigger" ? "Trigger" : (n.label ?? n.id)}${branch === "next" ? "" : branch === "true" ? " → Yes" : " → No"}` })),
  );
  const valid = slots.filter((s) => canConnect(definition, { source: s.source, branch: s.branch }, node.id).ok);
  return (
    <div className="border-t pt-3">
      <Field label="Not connected — run after" htmlFor={`${id}-connect`} error={valid.length ? undefined : "No free place to connect this step."}>
        <Select
          id={`${id}-connect`}
          value=""
          disabled={readOnly || !valid.length}
          onChange={(e) => {
            const slot = valid.find((s) => `${s.source}:${s.branch}` === e.target.value);
            if (slot) onConnect({ source: slot.source, branch: slot.branch });
          }}
        >
          <option value="">Choose a step…</option>
          {valid.map((s) => (
            <option key={`${s.source}:${s.branch}`} value={`${s.source}:${s.branch}`}>
              {s.label}
            </option>
          ))}
        </Select>
      </Field>
    </div>
  );
}
