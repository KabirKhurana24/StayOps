"use client";

import { Tooltip } from "@/components/ui/menu";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useRealtime } from "./realtime-provider";

const LABEL = { live: "Live", connecting: "Connecting", offline: "Offline" };
const HINT = {
  live: "Changes from other operators appear instantly.",
  connecting: "Connecting to live updates…",
  offline: "Live updates paused — data refreshes when you return to the tab.",
};

export function RealtimeIndicator({ compact = false }: { compact?: boolean }) {
  const { status, lastChangeAt } = useRealtime();
  const hint = lastChangeAt && status === "live" ? `${HINT.live} Last update ${formatRelative(new Date(lastChangeAt).toISOString())}.` : HINT[status];
  return (
    <Tooltip content={hint} side="bottom">
      <div
        className="flex h-7 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground"
        data-testid="realtime-status"
        data-status={status}
        role="status"
        aria-label={`Realtime: ${LABEL[status]}`}
      >
        <span className="relative inline-flex size-1.5" aria-hidden>
          {status === "live" ? <span className="absolute inline-flex size-full animate-ping rounded-full bg-success/40 [animation-duration:2.4s] motion-reduce:hidden" /> : null}
          <span
            className={cn(
              "relative inline-flex size-1.5 rounded-full",
              status === "live" && "bg-success",
              status === "connecting" && "animate-pulse bg-warning",
              status === "offline" && "bg-danger",
            )}
          />
        </span>
        <span className={cn(compact && "sr-only")}>{LABEL[status]}</span>
      </div>
    </Tooltip>
  );
}
