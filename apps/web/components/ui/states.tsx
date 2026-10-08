import { AlertTriangle, Inbox, RotateCw, type LucideIcon, WifiOff } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-[5px] bg-muted", className)} />;
}

/** Table-shaped placeholder: keeps layout stable while data loads. */
export function SkeletonRows({ rows = 5, columns = 4, className }: { rows?: number; columns?: number; className?: string }) {
  return (
    <div className={cn("divide-y", className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-3">
          <div className="flex min-w-0 flex-[2] flex-col gap-1.5">
            <Skeleton className="h-3 w-3/5" />
            <Skeleton className="h-2.5 w-2/5" />
          </div>
          {Array.from({ length: columns - 1 }, (_, c) => (
            <Skeleton key={c} className={cn("hidden h-3 flex-1 sm:block", c === columns - 2 && "max-w-16")} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function SkeletonBlock({ className }: { className?: string }) {
  return (
    <div className={cn("rounded-lg border bg-surface p-4 shadow-xs", className)} aria-busy="true" aria-label="Loading">
      <Skeleton className="h-3 w-32" />
      <Skeleton className="mt-4 h-3 w-full" />
      <Skeleton className="mt-2 h-3 w-4/5" />
      <Skeleton className="mt-2 h-3 w-3/5" />
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon: Icon = Inbox,
  compact = false,
  className,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: LucideIcon;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-6 text-center", compact ? "py-8" : "py-16", className)}>
      <div className={cn("mb-3 grid place-items-center rounded-lg border bg-surface-raised text-subtle-foreground shadow-xs", compact ? "size-8" : "size-10")}>
        <Icon className={compact ? "size-4" : "size-[18px]"} aria-hidden />
      </div>
      <p className="text-[13px] font-medium">{title}</p>
      {description ? <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  title,
  compact = false,
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  compact?: boolean;
}) {
  const apiError = error instanceof ApiError ? error : null;
  const offline = apiError?.code === "network_error";
  const Icon = offline ? WifiOff : AlertTriangle;
  const heading = title ?? (offline ? "Can't reach StayOps" : "This view couldn't load");
  const message = apiError?.message ?? "Something went wrong while loading this view.";
  return (
    <div role="alert" className={cn("flex flex-col items-center justify-center px-6 text-center", compact ? "py-8" : "py-14")}>
      <div className="mb-3 grid size-10 place-items-center rounded-lg border border-danger/20 bg-danger/5 text-danger">
        <Icon className="size-[18px]" aria-hidden />
      </div>
      <p className="text-[13px] font-medium">{heading}</p>
      <p className="mt-1 max-w-sm text-xs text-muted-foreground">{message}</p>
      {apiError?.requestId ? (
        <p className="mt-2 font-mono text-[11px] text-subtle-foreground">Request {apiError.requestId}</p>
      ) : null}
      {onRetry ? (
        <Button size="sm" className="mt-4" onClick={onRetry}>
          <RotateCw /> Try again
        </Button>
      ) : null}
    </div>
  );
}

/** Small "coming soon" placeholder for modules that are intentionally not built yet. */
export function ComingSoon({ title, description, icon }: { title: string; description: string; icon?: LucideIcon }) {
  return (
    <EmptyState
      icon={icon}
      title={title}
      description={description}
      action={<span className="rounded-[5px] border px-1.5 py-px text-[11px] font-medium text-muted-foreground">Planned</span>}
    />
  );
}
