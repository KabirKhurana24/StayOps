import type { Metadata } from "next";
import { Suspense } from "react";

import { SkeletonRows } from "@/components/ui/states";
import { CleaningPage } from "@/features/cleaning/cleaning-page";

export const metadata: Metadata = { title: "Cleaning" };

export default function Page() {
  return (
    <Suspense fallback={<SkeletonRows rows={10} />}>
      <CleaningPage />
    </Suspense>
  );
}
