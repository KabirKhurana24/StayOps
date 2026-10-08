import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** A working surface with a compact header. The basic layout unit of every screen. */
export function Panel({
  title,
  description,
  count,
  icon,
  actions,
  children,
  className,
  bodyClassName,
  headerClassName,
  id,
}: {
  title: string;
  description?: ReactNode;
  count?: number | string;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  headerClassName?: string;
  id?: string;
}) {
  return (
    <section
      aria-labelledby={id ? `${id}-title` : undefined}
      className={cn("flex min-w-0 flex-col overflow-hidden rounded-lg border bg-surface shadow-xs", className)}
    >
      <header className={cn("flex min-h-11 items-center justify-between gap-3 border-b px-4 py-2", headerClassName)}>
        <div className="flex min-w-0 items-center gap-2">
          {icon ? <span className="text-muted-foreground [&_svg]:size-4">{icon}</span> : null}
          <div className="min-w-0">
            <h2 id={id ? `${id}-title` : undefined} className="flex items-center gap-2 text-[13px] font-semibold">
              <span>{title}</span>
              {count !== undefined ? (
                <span className="rounded-[4px] bg-muted px-1.5 text-[11px] leading-[18px] font-medium text-muted-foreground tabular">{count}</span>
              ) : null}
            </h2>
            {description ? <p className="truncate text-xs text-muted-foreground">{description}</p> : null}
          </div>
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
      </header>
      <div className={cn("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </section>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow ? <div className="mb-1 text-xs text-muted-foreground">{eyebrow}</div> : null}
        <h1 className="text-xl font-semibold tracking-[-0.01em] text-balance">{title}</h1>
        {description ? <p className="mt-1 text-[13px] text-muted-foreground">{description}</p> : null}
        {children}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** Label/value rows for detail views. */
export function DescriptionList({ children, className }: { children: ReactNode; className?: string }) {
  return <dl className={cn("divide-y", className)}>{children}</dl>;
}

export function DescriptionItem({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={cn("grid grid-cols-[120px_minmax(0,1fr)] gap-3 px-4 py-2.5 text-[13px]", className)}>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}
