import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/** Compact status label. Square-ish corners: badges label state, they are not buttons. */
const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-[5px] border px-1.5 py-px text-[11px] font-medium leading-4 whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "border-border bg-muted/70 text-muted-foreground",
        info: "border-info/15 bg-info/8 text-info",
        success: "border-success/15 bg-success/8 text-success",
        warning: "border-warning/20 bg-warning/8 text-warning",
        danger: "border-danger/15 bg-danger/7 text-danger",
        accent: "border-accent/15 bg-accent-soft text-accent",
        outline: "border-border-strong bg-transparent text-muted-foreground",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

const DOT = {
  neutral: "bg-subtle-foreground",
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  accent: "bg-accent",
  outline: "bg-subtle-foreground",
} as const;

export function Badge({
  className,
  tone,
  dot = false,
  children,
  ...props
}: ComponentProps<"span"> & VariantProps<typeof badgeVariants> & { dot?: boolean }) {
  return (
    <span className={cn(badgeVariants({ tone }), className)} {...props}>
      {dot ? <span aria-hidden className={cn("size-1.5 rounded-full", DOT[tone ?? "neutral"])} /> : null}
      {children}
    </span>
  );
}
