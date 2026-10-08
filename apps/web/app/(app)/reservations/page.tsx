import type { Metadata } from "next";
import { Suspense } from "react";

import { SkeletonRows } from "@/components/ui/states";
import { ReservationsPage } from "@/features/reservations/reservations-page";

export const metadata: Metadata = { title: "Reservations" };

export default function Page() {
  return (
    <Suspense fallback={<SkeletonRows rows={10} />}>
      <ReservationsPage />
    </Suspense>
  );
}
