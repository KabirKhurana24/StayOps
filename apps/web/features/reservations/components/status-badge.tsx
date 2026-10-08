import type { ReservationSource, ReservationStatus } from "@stayops/shared";

import { Badge } from "@/components/ui/badge";
import { SOURCE_LABEL, STATUS_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

export const STATUS_TONE = {
  PENDING: "warning",
  CONFIRMED: "info",
  CHECKED_IN: "success",
  CHECKED_OUT: "neutral",
  CANCELLED: "danger",
} as const satisfies Record<ReservationStatus, string>;

export function StatusBadge({ status, className }: { status: ReservationStatus; className?: string }) {
  return (
    <Badge tone={STATUS_TONE[status]} dot className={className}>
      {STATUS_LABEL[status]}
    </Badge>
  );
}

const SOURCE_SWATCH: Record<ReservationSource, string> = {
  AIRBNB: "bg-[#e5484d]",
  BOOKING_COM: "bg-[#1f5fbf]",
  VRBO: "bg-[#2b6fd6]",
  EXPEDIA: "bg-[#d9a400]",
  DIRECT: "bg-accent",
  OTHER: "bg-subtle-foreground",
};

/** Channel name with a small identifying swatch (no logos, no loud colour). */
export function SourceLabel({ source, className }: { source: ReservationSource; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-muted-foreground", className)}>
      <span aria-hidden className={cn("size-1.5 rounded-[2px]", SOURCE_SWATCH[source])} />
      {SOURCE_LABEL[source]}
    </span>
  );
}
