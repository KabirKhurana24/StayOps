import type { Metadata } from "next";
import { Suspense } from "react";

import { SkeletonRows } from "@/components/ui/states";
import { CopilotPage } from "@/features/copilot/copilot-page";

export const metadata: Metadata = { title: "Copilot" };

export default function Page() {
  return (
    <Suspense fallback={<SkeletonRows rows={6} />}>
      <CopilotPage />
    </Suspense>
  );
}
