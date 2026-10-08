"use client";

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Compact single-choice control (view switchers, category filters). */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
  className,
  size = "md",
}: {
  value: T;
  onChange: (value: T) => void;
  options: readonly { value: T; label: ReactNode; icon?: ReactNode; count?: number }[];
  label: string;
  className?: string;
  size?: "sm" | "md";
}) {
  return (
    <div role="radiogroup" aria-label={label} className={cn("inline-flex items-center rounded-md border bg-muted/60 p-0.5", className)}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={cn(
              "flex items-center gap-1.5 rounded-[5px] px-2.5 font-medium whitespace-nowrap transition-[background-color,color,box-shadow] duration-150 [&_svg]:size-3.5",
              size === "sm" ? "h-6 text-xs" : "h-7 text-[13px]",
              active ? "bg-surface text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {o.icon}
            <span>{o.label}</span>
            {o.count !== undefined ? <span className="text-[11px] text-subtle-foreground tabular">{o.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

/** Underline tabs for sections within a page. */
export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
  label,
  className,
}: {
  value: T;
  onChange: (value: T) => void;
  tabs: readonly { value: T; label: string; count?: number; disabled?: boolean; badge?: string }[];
  label: string;
  className?: string;
}) {
  return (
    <div role="tablist" aria-label={label} className={cn("scrollbar-thin flex gap-1 overflow-x-auto border-b", className)}>
      {tabs.map((t) => {
        const active = t.value === value;
        return (
          <button
            key={t.value}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={t.disabled}
            onClick={() => onChange(t.value)}
            className={cn(
              "-mb-px flex h-9 items-center gap-1.5 border-b-2 px-2.5 text-[13px] whitespace-nowrap transition-colors duration-150 disabled:cursor-not-allowed",
              active ? "border-accent font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
              t.disabled && "opacity-55 hover:text-muted-foreground",
            )}
          >
            <span>{t.label}</span>
            {t.count !== undefined ? (
              <span className={cn("rounded-[4px] px-1.5 text-[11px] leading-[18px] tabular", active ? "bg-accent-soft text-accent" : "bg-muted text-muted-foreground")}>{t.count}</span>
            ) : null}
            {t.badge ? <span className="rounded-[4px] border px-1 text-[10px] leading-4 text-subtle-foreground">{t.badge}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
