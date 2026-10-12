"use client";

import type { OperationalSignal } from "@stayops/shared";
import { Bell, CheckCircle2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { useCommandCenter } from "@/features/activity/api";
import { SEVERITY_STYLE } from "@/features/command-center/signals";
import { cn } from "@/lib/utils";

/** Topbar bell: the open operational signals, straight from persisted state. */
export function Notifications() {
  const { data } = useCommandCenter();
  const [open, setOpen] = useState(false);
  const signals: OperationalSignal[] = data?.signals.filter((s) => s.severity !== "info") ?? [];
  const count = signals.length;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className="relative grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground data-[state=open]:bg-muted"
        aria-label={count ? `Notifications: ${count} need attention` : "Notifications"}
      >
        <Bell className="size-4" />
        {count ? (
          <span className="absolute top-1 right-1 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-danger px-1 text-[9px] leading-none font-semibold text-white tabular">
            {count > 9 ? "9+" : count}
          </span>
        ) : null}
      </PopoverTrigger>
      <PopoverContent className="w-[360px]">
        <div className="flex items-center justify-between border-b px-3 py-2.5">
          <div className="text-[13px] font-semibold">Needs attention</div>
          <Link href="/" onClick={() => setOpen(false)} className="text-xs text-accent hover:underline">
            Command Center
          </Link>
        </div>
        {count === 0 ? (
          <div className="flex flex-col items-center px-6 py-8 text-center">
            <CheckCircle2 className="mb-2 size-5 text-success" aria-hidden />
            <p className="text-[13px] font-medium">All clear</p>
            <p className="mt-0.5 text-xs text-muted-foreground">No operational issues right now.</p>
          </div>
        ) : (
          <ul className="scrollbar-thin max-h-[360px] divide-y overflow-y-auto">
            {signals.map((s, i) => {
              const style = SEVERITY_STYLE[s.severity];
              const body = (
                <div className="flex gap-2.5 px-3 py-2.5">
                  <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", style.dot)} aria-hidden />
                  <div className="min-w-0">
                    <div className="text-[13px] font-medium">{s.title}</div>
                    <div className="line-clamp-2 text-xs text-muted-foreground">{s.detail}</div>
                  </div>
                </div>
              );
              return (
                <li key={`${s.code}-${s.alert_id ?? s.reservation_id ?? i}`}>
                  {s.reservation_id || s.href ? (
                    <Link href={s.href ?? `/reservations?r=${s.reservation_id}`} onClick={() => setOpen(false)} className="block hover:bg-muted/50">
                      {body}
                    </Link>
                  ) : (
                    body
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}
