import { AlertCircle, ChevronDown, Search, X } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

const fieldBase =
  "w-full min-w-0 rounded-md border border-input bg-surface px-2.5 text-[13px] text-foreground shadow-xs transition-[border-color,box-shadow] duration-150 placeholder:text-subtle-foreground hover:border-border-strong focus-visible:border-accent focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-accent/15 disabled:cursor-not-allowed disabled:bg-muted/50 disabled:opacity-70 aria-invalid:border-danger aria-invalid:ring-danger/10";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(fieldBase, "h-8", className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn(fieldBase, "min-h-20 py-1.5 leading-relaxed", className)} {...props} />;
}

/** Native select (keyboard + mobile friendly) with a themed chevron instead of the OS arrow. */
export function Select({ className, children, wrapperClassName, ...props }: ComponentProps<"select"> & { wrapperClassName?: string }) {
  return (
    <div className={cn("relative min-w-0", wrapperClassName)}>
      <select className={cn(fieldBase, "h-8 cursor-pointer appearance-none pr-7", className)} {...props}>
        {children}
      </select>
      <ChevronDown aria-hidden className="pointer-events-none absolute top-1/2 right-2 size-3.5 -translate-y-1/2 text-subtle-foreground" />
    </div>
  );
}

export function SearchInput({
  className,
  wrapperClassName,
  onClear,
  ...props
}: ComponentProps<"input"> & { wrapperClassName?: string; onClear?: () => void }) {
  return (
    <div className={cn("relative min-w-0", wrapperClassName)}>
      <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-subtle-foreground" />
      <input type="search" className={cn(fieldBase, "h-8 pl-8 [&::-webkit-search-cancel-button]:hidden", onClear && props.value ? "pr-7" : "", className)} {...props} />
      {onClear && props.value ? (
        <button
          type="button"
          onClick={onClear}
          aria-label="Clear search"
          className="absolute top-1/2 right-1.5 grid size-5 -translate-y-1/2 place-items-center rounded-sm text-subtle-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-3" />
        </button>
      ) : null}
    </div>
  );
}

export function Label({ className, ...props }: ComponentProps<"label">) {
  return <label className={cn("text-xs font-medium text-muted-foreground", className)} {...props} />;
}

export function Field({
  label,
  htmlFor,
  error,
  hint,
  children,
  className,
}: {
  label: string;
  htmlFor: string;
  error?: string;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {error ? (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-subtle-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

export function FormError({ message, children }: { message?: string | null; children?: ReactNode }) {
  if (!message) return null;
  return (
    <div role="alert" className="flex gap-2 rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-[13px] text-danger">
      <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <div className="min-w-0">
        <p>{message}</p>
        {children}
      </div>
    </div>
  );
}

export function FormSection({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <fieldset className={cn("space-y-3", className)}>
      <legend className="eyebrow mb-2">{title}</legend>
      {children}
    </fieldset>
  );
}
