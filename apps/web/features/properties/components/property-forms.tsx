"use client";

import { type PropertyInput, propertyInputSchema, UNIT_STATUSES, UNIT_TYPES, type UnitStatus, type UnitType } from "@stayops/shared";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, FormError, Input, Select } from "@/components/ui/form";
import { useCreateUnit } from "@/features/properties/api";
import { ApiError } from "@/lib/api/client";

const TIMEZONES = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : ["UTC"];

export function PropertyForm({
  initial,
  submitLabel,
  pending,
  onSubmit,
  onCancel,
  error,
}: {
  initial?: Partial<PropertyInput>;
  submitLabel: string;
  pending: boolean;
  onSubmit: (values: PropertyInput) => void;
  onCancel?: () => void;
  error?: string | null;
}) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const handle = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const parsed = propertyInputSchema.safeParse(Object.fromEntries(new FormData(event.currentTarget).entries()));
    if (!parsed.success) {
      setErrors(Object.fromEntries(parsed.error.issues.map((i) => [String(i.path[0]), i.message])));
      return;
    }
    setErrors({});
    onSubmit(parsed.data);
  };
  return (
    <form onSubmit={handle} className="space-y-4" noValidate>
      <Field label="Name" htmlFor="name" error={errors.name}>
        <Input id="name" name="name" defaultValue={initial?.name} placeholder="Canal View Apartments" autoFocus />
      </Field>
      <Field label="Address" htmlFor="address" error={errors.address}>
        <Input id="address" name="address" defaultValue={initial?.address} placeholder="Street, city, country" />
      </Field>
      <Field label="Timezone" htmlFor="timezone" error={errors.timezone} hint="Check-in/out times, “today” and the calendar use this zone.">
        <Select id="timezone" name="timezone" defaultValue={initial?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone}>
          {TIMEZONES.map((tz) => (
            <option key={tz} value={tz}>
              {tz.replaceAll("_", " ")}
            </option>
          ))}
        </Select>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Default check-in" htmlFor="default_check_in_time" error={errors.default_check_in_time}>
          <Input id="default_check_in_time" name="default_check_in_time" type="time" defaultValue={initial?.default_check_in_time ?? "15:00"} />
        </Field>
        <Field label="Default check-out" htmlFor="default_check_out_time" error={errors.default_check_out_time}>
          <Input id="default_check_out_time" name="default_check_out_time" type="time" defaultValue={initial?.default_check_out_time ?? "11:00"} />
        </Field>
      </div>
      <FormError message={error} />
      <div className="flex justify-end gap-2 pt-1">
        {onCancel ? <Button onClick={onCancel}>Cancel</Button> : null}
        <Button type="submit" variant="primary" disabled={pending}>
          {pending ? "Saving…" : submitLabel}
        </Button>
      </div>
    </form>
  );
}

export function UnitForm({ propertyId, onDone }: { propertyId: string; onDone: () => void }) {
  const create = useCreateUnit();
  const [nameError, setNameError] = useState<string>();
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const f = new FormData(event.currentTarget);
    const name = String(f.get("name") ?? "").trim();
    if (!name) return setNameError("Unit name is required");
    setNameError(undefined);
    create.mutate(
      { property_id: propertyId, name, type: f.get("type") as UnitType, status: f.get("status") as UnitStatus },
      {
        onSuccess: (u) => {
          toast.success(`Unit ${u.name} added`);
          onDone();
        },
      },
    );
  };
  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <Field label="Unit name or number" htmlFor="unit-name" error={nameError}>
        <Input id="unit-name" name="name" placeholder="101" autoFocus />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Type" htmlFor="unit-type">
          <Select id="unit-type" name="type" defaultValue="APARTMENT">
            {UNIT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t.charAt(0) + t.slice(1).toLowerCase()}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Status" htmlFor="unit-status">
          <Select id="unit-status" name="status" defaultValue="ACTIVE">
            {UNIT_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s.charAt(0) + s.slice(1).toLowerCase()}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <FormError message={create.error instanceof ApiError ? create.error.message : null} />
      <div className="flex justify-end gap-2 pt-1">
        <Button onClick={onDone}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={create.isPending}>
          {create.isPending ? "Saving…" : "Add unit"}
        </Button>
      </div>
    </form>
  );
}
