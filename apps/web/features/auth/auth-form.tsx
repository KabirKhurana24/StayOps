"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Field, FormError, Input } from "@/components/ui/form";
import { isSupabaseConfigured } from "@/lib/env";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

const credentialsSchema = z.object({
  email: z.email("Enter a valid email"),
  password: z.string().min(8, "Use at least 8 characters"),
  fullName: z.string().trim().max(200).optional(),
});

/** Only allow same-origin relative redirects (prevents open redirects). */
function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    const parsed = credentialsSchema.safeParse({
      email: form.get("email"),
      password: form.get("password"),
      fullName: form.get("fullName") ?? undefined,
    });
    if (!parsed.success) {
      setFieldErrors(Object.fromEntries(parsed.error.issues.map((i) => [String(i.path[0]), i.message])));
      return;
    }
    setFieldErrors({});
    setBusy(true);
    const supabase = getSupabaseBrowserClient();
    try {
      if (mode === "login") {
        const { error: authError } = await supabase.auth.signInWithPassword({
          email: parsed.data.email,
          password: parsed.data.password,
        });
        if (authError) throw authError;
        router.replace(safeNext(params.get("next")));
        router.refresh();
      } else {
        const { data, error: authError } = await supabase.auth.signUp({
          email: parsed.data.email,
          password: parsed.data.password,
          options: {
            data: { full_name: parsed.data.fullName || undefined },
            emailRedirectTo: `${window.location.origin}/auth/callback?next=/onboarding`,
          },
        });
        if (authError) throw authError;
        if (data.session) {
          router.replace("/onboarding");
          router.refresh();
        } else {
          setNotice("Check your inbox to confirm your email, then sign in.");
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="w-full max-w-sm rounded-lg border bg-surface p-6 shadow-xs">
      <div className="mb-5">
        <div className="text-[13px] font-bold tracking-[0.18em]">STAYOPS</div>
        <h1 className="mt-3 text-base font-semibold">
          {mode === "login" ? "Sign in to Operations" : "Create your operator account"}
        </h1>
      </div>
      {!isSupabaseConfigured ? (
        <FormError message="Supabase is not configured. Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY in .env and restart." />
      ) : null}
      {notice ? (
        <div className="rounded-md border border-success/30 bg-success/5 px-3 py-2 text-[13px] text-success" role="status">
          {notice}
        </div>
      ) : (
        <form onSubmit={onSubmit} className="space-y-3" noValidate>
          {mode === "signup" ? (
            <Field label="Full name" htmlFor="fullName" error={fieldErrors.fullName}>
              <Input id="fullName" name="fullName" autoComplete="name" />
            </Field>
          ) : null}
          <Field label="Email" htmlFor="email" error={fieldErrors.email}>
            <Input id="email" name="email" type="email" autoComplete="email" required aria-invalid={!!fieldErrors.email} />
          </Field>
          <Field label="Password" htmlFor="password" error={fieldErrors.password}>
            <Input
              id="password"
              name="password"
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              required
              aria-invalid={!!fieldErrors.password}
            />
          </Field>
          <FormError message={error} />
          <Button type="submit" variant="primary" className="w-full" disabled={busy || !isSupabaseConfigured}>
            {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          </Button>
        </form>
      )}
      <p className="mt-4 text-center text-xs text-muted-foreground">
        {mode === "login" ? (
          <>
            New to StayOps? <Link className="text-accent hover:underline" href="/signup">Create an account</Link>
          </>
        ) : (
          <>
            Already have an account? <Link className="text-accent hover:underline" href="/login">Sign in</Link>
          </>
        )}
      </p>
    </div>
  );
}
