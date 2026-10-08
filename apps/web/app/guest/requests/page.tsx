import type { Metadata } from "next";
import { Suspense } from "react";

import { GuestRequests } from "@/features/guest-portal/guest-requests";

export const metadata: Metadata = { title: "Requests" };

export default function Page() {
  return (
    <Suspense>
      <GuestRequests />
    </Suspense>
  );
}
