"use client";

import { ArrowLeft, SearchX } from "lucide-react";
import Link from "next/link";

import { useBreadcrumb } from "@/components/shell/shell-context";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { useReservation } from "@/features/reservations/api";
import { ApiError } from "@/lib/api/client";
import { guestName } from "@/lib/format";

import { ReservationDetailView } from "./components/reservation-detail-view";

/** Full-page reservation detail (deep links); the drawer reuses the same view. */
export function ReservationDetail({ id }: { id: string }) {
  const reservation = useReservation(id);
  const org = useCurrentOrganization();
  useBreadcrumb(reservation.data ? `${guestName(reservation.data.guest)} · ${reservation.data.confirmation_code}` : null);

  return (
    <div>
      <Link href="/reservations" className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> All reservations
      </Link>
      {reservation.isPending ? (
        <div className="space-y-3" aria-busy="true" aria-label="Loading reservation">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-72 w-full" />
        </div>
      ) : reservation.error ? (
        reservation.error instanceof ApiError && reservation.error.status === 404 ? (
          <EmptyState icon={SearchX} title="Reservation not found" description="It may have been removed, or it belongs to another organization." />
        ) : (
          <ErrorState error={reservation.error} onRetry={() => reservation.refetch()} />
        )
      ) : (
        <ReservationDetailView r={reservation.data} canWrite={can(org.data, "reservations:write")} variant="page" />
      )}
    </div>
  );
}
