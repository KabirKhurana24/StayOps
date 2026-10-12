import type { Metadata } from "next";
import { Suspense } from "react";

import { SkeletonRows } from "@/components/ui/states";
import { AutomationsPage } from "@/features/automations/automations-page";

export const metadata: Metadata = { title: "Autopilot" };

export default function Page() {
  return (
    <Suspense fallback={<SkeletonRows rows={6} />}>
      <AutomationsPage />
    </Suspense>
  );
}
