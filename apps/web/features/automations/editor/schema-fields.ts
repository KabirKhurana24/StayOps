/**
 * Turn the server's pydantic JSON schemas (catalog input/config schemas) into a
 * flat list of typed form fields. Only the shapes the catalog uses are handled.
 */
export type FormFieldKind = "text" | "textarea" | "number" | "boolean" | "enum" | "enum_multi" | "member" | "property" | "property_multi" | "time";

export type FormField = {
  name: string;
  title: string;
  kind: FormFieldKind;
  required: boolean;
  options: string[];
  min?: number;
  max?: number;
  maxLength?: number;
  default?: unknown;
  description?: string;
};

type Schema = Record<string, unknown>;

const LONG_TEXT = new Set(["description", "detail", "content", "note", "reason"]);
const MEMBER = new Set(["assignee_id", "assign_to"]);

function resolve(node: Schema, root: Schema): Schema {
  const ref = node.$ref as string | undefined;
  if (ref?.startsWith("#/$defs/")) {
    const defs = (root.$defs ?? {}) as Record<string, Schema>;
    return { ...defs[ref.slice("#/$defs/".length)], ...Object.fromEntries(Object.entries(node).filter(([k]) => k !== "$ref")) };
  }
  return node;
}

/** Unwrap `anyOf: [X, {type: null}]` (optional fields). */
function unwrap(node: Schema, root: Schema): Schema {
  const any = node.anyOf as Schema[] | undefined;
  if (any) {
    const real = any.filter((s) => s.type !== "null");
    if (real.length === 1) return { ...resolve(real[0], root), ...Object.fromEntries(Object.entries(node).filter(([k]) => k !== "anyOf")) };
  }
  return resolve(node, root);
}

const titleCase = (name: string) => name.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());

export function schemaFields(schema: Schema): FormField[] {
  const props = (schema.properties ?? {}) as Record<string, Schema>;
  const required = new Set((schema.required as string[] | undefined) ?? []);
  return Object.entries(props).map(([name, raw]) => {
    const s = unwrap(raw, schema);
    const base = {
      name,
      title: (raw.title as string | undefined) ?? titleCase(name),
      required: required.has(name),
      options: [] as string[],
      default: raw.default ?? s.default,
      description: (raw.description ?? s.description) as string | undefined,
    };
    if (s.type === "array") {
      const items = resolve((s.items ?? {}) as Schema, schema);
      if (name === "property_ids") return { ...base, kind: "property_multi" as const };
      return { ...base, kind: "enum_multi" as const, options: (items.enum as string[] | undefined) ?? [] };
    }
    if (s.enum) return { ...base, kind: "enum" as const, options: s.enum as string[] };
    if (s.type === "boolean") return { ...base, kind: "boolean" as const };
    if (s.type === "integer" || s.type === "number") {
      return { ...base, kind: "number" as const, min: s.minimum as number | undefined, max: s.maximum as number | undefined };
    }
    if (MEMBER.has(name)) return { ...base, kind: "member" as const };
    if (name === "property_id") return { ...base, kind: "property" as const };
    if (name === "local_time") return { ...base, kind: "time" as const };
    return { ...base, kind: LONG_TEXT.has(name) ? ("textarea" as const) : ("text" as const), maxLength: s.maxLength as number | undefined };
  });
}

const NICE: Record<string, string> = {
  subject: "Where the trigger happened",
  property: "A chosen property",
  subject_task: "The trigger's task",
  turnover_task: "The stay's turnover cleaning",
  escalate_only: "Only raise, never lower",
  set: "Set exactly",
};

export const ENUM_LABEL = (value: string) =>
  NICE[value] ?? value.replaceAll("_", " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
