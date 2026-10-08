import type { Metadata } from "next";
import { Suspense } from "react";

import { SkeletonRows } from "@/components/ui/states";
import { MaintenancePage } from "@/features/maintenance/maintenance-page";

export const metadata: Metadata = { title: "Maintenance" };

export default function Page() {
  return (
    <Suspense fallback={<SkeletonRows rows={10} />}>
      <MaintenancePage />
    </Suspense>
  );
}
