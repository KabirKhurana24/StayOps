import type { Metadata } from "next";
import { Suspense } from "react";

import { SkeletonRows } from "@/components/ui/states";
import { InboxPage } from "@/features/inbox/inbox-page";

export const metadata: Metadata = { title: "Inbox" };

export default function Page() {
  return (
    <Suspense fallback={<SkeletonRows rows={10} />}>
      <InboxPage />
    </Suspense>
  );
}
