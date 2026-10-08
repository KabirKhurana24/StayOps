"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, FormError, Input } from "@/components/ui/form";
import { useCreateOrganization } from "@/features/organizations/api";
import { ApiError } from "@/lib/api/client";

export default function OnboardingPage() {
  const router = useRouter();
  const create = useCreateOrganization();
  const [nameError, setNameError] = useState<string>();

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const f = new FormData(event.currentTarget);
    const name = String(f.get("name") ?? "").trim();
    const currency = String(f.get("currency") ?? "USD").trim().toUpperCase();
    if (!name) return setNameError("Give your organization a name");
    setNameError(undefined);
    create.mutate({ name, default_currency: currency }, { onSuccess: () => router.replace("/") });
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm rounded-lg border bg-surface p-6">
        <div className="text-[13px] font-bold tracking-[0.18em]">STAYOPS</div>
        <h1 className="mt-3 text-base font-semibold">Set up your organization</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">You&apos;ll be its owner. Teammates can be invited later.</p>
        <form onSubmit={submit} className="mt-4 space-y-3" noValidate>
          <Field label="Organization name" htmlFor="name" error={nameError}>
            <Input id="name" name="name" placeholder="Canal Stays" />
          </Field>
          <Field label="Default currency" htmlFor="currency">
            <Input id="currency" name="currency" defaultValue="EUR" maxLength={3} className="uppercase" />
          </Field>
          <FormError message={create.error instanceof ApiError ? create.error.message : null} />
          <Button type="submit" variant="primary" className="w-full" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create organization"}
          </Button>
        </form>
      </div>
    </div>
  );
}
