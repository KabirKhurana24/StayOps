import type { OperationalSignal } from "@stayops/shared";
import { CircleCheck, ShieldAlert } from "lucide-react";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { cn } from "@/lib/utils";

import { SEVERITY_STYLE, signalHref, signalMeta } from "../signals";

function SignalItem({ signal }: { signal: OperationalSignal }) {
  const style = SEVERITY_STYLE[signal.severity];
  const meta = signalMeta(signal);
  const href = signalHref(signal);
  return (
    <li className="relative flex gap-3 py-3 pr-4 pl-5" data-testid="signal" data-code={signal.code} data-severity={signal.severity}>
      <span aria-hidden className={cn("absolute top-3 bottom-3 left-0 w-[3px] rounded-r-full", style.bar)} />
      <div className="min-w-0 flex-1">
        <div className={cn("text-[11px] font-medium", style.text)}>
          <span className="sr-only">{style.label}: </span>
          {meta.category}
        </div>
        <div className="mt-0.5 text-[13px] leading-snug font-medium">{signal.title}</div>
        <div className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{signal.detail}</div>
      </div>
      {href ? (
        <Link
          href={href}
          className={cn(buttonVariants({ size: "xs", variant: "secondary" }), "mt-4 self-start")}
          aria-label={`${meta.action}: ${signal.title}`}
        >
          {meta.action}
        </Link>
      ) : null}
    </li>
  );
}

export function NeedsAttention({ signals }: { signals: OperationalSignal[] }) {
  const open = signals.filter((s) => s.severity !== "info").length;
  return (
    <Panel
      id="needs-attention"
      title="Needs attention"
      count={open || undefined}
      icon={<ShieldAlert />}
      description={signals.length ? "Derived live from reservations, tasks and maintenance" : undefined}
      bodyClassName="xl:max-h-[calc(100vh-150px)] xl:overflow-y-auto scrollbar-thin"
    >
      {signals.length === 0 ? (
        <div className="flex flex-col items-center px-6 py-10 text-center">
          <CircleCheck className="mb-2 size-5 text-success" aria-hidden />
          <p className="text-[13px] font-medium">All clear</p>
          <p className="mt-0.5 text-xs text-muted-foreground">Nothing needs attention right now.</p>
        </div>
      ) : (
        <ul className="divide-y">
          {signals.map((s, i) => (
            <SignalItem key={`${s.code}-${s.reservation_id ?? i}`} signal={s} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
