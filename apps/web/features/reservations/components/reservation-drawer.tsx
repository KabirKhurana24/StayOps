"use client";

import { SearchX } from "lucide-react";

import { Sheet } from "@/components/ui/sheet";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { useReservation } from "@/features/reservations/api";
import { ApiError } from "@/lib/api/client";

import { ReservationDetailView } from "./reservation-detail-view";

function DrawerBody({ id }: { id: string }) {
  const reservation = useReservation(id);
  const org = useCurrentOrganization();
  if (reservation.isPending) {
    return (
      <div className="space-y-4 p-5" aria-busy="true" aria-label="Loading reservation">
        <div className="flex gap-3">
          <Skeleton className="size-11 rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-3 w-28" />
          </div>
        </div>
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  if (reservation.error) {
    if (reservation.error instanceof ApiError && reservation.error.status === 404) {
      return <EmptyState icon={SearchX} title="Reservation not found" description="It may belong to another organization." />;
    }
    return <ErrorState error={reservation.error} onRetry={() => reservation.refetch()} />;
  }
  return <ReservationDetailView r={reservation.data} canWrite={can(org.data, "reservations:write")} />;
}

/** Right-hand reservation detail drawer, keyed by id (URL-driven by the caller). */
export function ReservationDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  return (
    <Sheet open={Boolean(id)} onOpenChange={(open) => !open && onClose()} title="Reservation details" size="lg" hideTitle bodyClassName="p-0">
      {id ? <DrawerBody id={id} /> : null}
    </Sheet>
  );
}
