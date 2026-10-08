import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const AVATAR_TONES = [
  "bg-[#e3efe9] text-[#1d6b4a]",
  "bg-[#e6eefa] text-[#2a59a8]",
  "bg-[#f6eadb] text-[#94570c]",
  "bg-[#efe7f6] text-[#6b3fa0]",
  "bg-[#f8e5e3] text-[#a3372e]",
  "bg-[#e3f0f1] text-[#0e6b64]",
];

function hash(value: string): number {
  let h = 0;
  for (let i = 0; i < value.length; i++) h = (h * 31 + value.charCodeAt(i)) | 0;
  return Math.abs(h);
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "")).toUpperCase();
}

/** Initials avatar with a stable colour per name. */
export function Avatar({ name, size = "md", className }: { name: string; size?: "xs" | "sm" | "md" | "lg"; className?: string }) {
  const sizes = { xs: "size-5 text-[9px]", sm: "size-6 text-[10px]", md: "size-8 text-[11px]", lg: "size-11 text-sm" };
  return (
    <span
      aria-hidden
      className={cn("inline-grid shrink-0 place-items-center rounded-full font-semibold", AVATAR_TONES[hash(name) % AVATAR_TONES.length], sizes[size], className)}
    >
      {initials(name)}
    </span>
  );
}

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd className={cn("inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[4px] border border-border-strong/70 bg-surface px-1 font-sans text-[10px] font-medium text-muted-foreground", className)}>
      {children}
    </kbd>
  );
}

/** One cell of a metric strip: label, a strong number, and supporting context. */
export function Metric({
  label,
  value,
  sub,
  tone,
  icon,
  muted = false,
  testId,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "danger" | "warning" | "success";
  icon?: ReactNode;
  muted?: boolean;
  testId?: string;
}) {
  return (
    <div className="min-w-0 px-4 py-3" data-testid={testId} data-value={typeof value === "number" ? value : undefined}>
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground [&_svg]:size-3.5">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      <div
        className={cn(
          "mt-1 text-[22px] leading-7 font-semibold tracking-[-0.02em] tabular",
          tone === "danger" && "text-danger",
          tone === "warning" && "text-warning",
          tone === "success" && "text-success",
          muted && "text-subtle-foreground",
        )}
      >
        {value}
      </div>
      {sub ? <div className="mt-0.5 truncate text-xs text-muted-foreground">{sub}</div> : null}
    </div>
  );
}

const STRIP_COLUMNS = {
  4: "grid-cols-2 lg:grid-cols-4",
  5: "grid-cols-2 sm:grid-cols-3 lg:grid-cols-5",
  6: "grid-cols-2 sm:grid-cols-3 xl:grid-cols-6",
  8: "grid-cols-2 sm:grid-cols-4 2xl:grid-cols-8",
} as const;

export function MetricStrip({
  children,
  label,
  className,
  columns = 6,
}: {
  children: ReactNode;
  label: string;
  className?: string;
  columns?: keyof typeof STRIP_COLUMNS;
}) {
  return (
    <section aria-label={label} className={cn("overflow-hidden rounded-lg border bg-border shadow-xs", className)}>
      {/* 1px gaps over a border-coloured background give clean dividers at every breakpoint. */}
      <div className={cn("grid gap-px [&>*]:bg-surface", STRIP_COLUMNS[columns])}>{children}</div>
    </section>
  );
}

/** Thin horizontal meter (occupancy etc.). */
export function Meter({ value, className, tone = "accent", label }: { value: number; className?: string; tone?: "accent" | "warning" | "danger"; label?: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      aria-label={label}
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}
    >
      <div
        className={cn("h-full rounded-full transition-[width] duration-500", tone === "accent" && "bg-accent", tone === "warning" && "bg-warning", tone === "danger" && "bg-danger")}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function LiveDot({ className }: { className?: string }) {
  return (
    <span className={cn("relative inline-flex size-1.5", className)} aria-hidden>
      <span className="absolute inline-flex size-full animate-ping rounded-full bg-success/50 motion-reduce:hidden" />
      <span className="relative inline-flex size-1.5 rounded-full bg-success" />
    </span>
  );
}
