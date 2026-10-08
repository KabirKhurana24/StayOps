import type { Metadata } from "next";
import { Suspense } from "react";

import { Skeleton } from "@/components/ui/states";
import { CalendarPage } from "@/features/calendar/calendar-page";

export const metadata: Metadata = { title: "Calendar" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full" />}>
      <CalendarPage />
    </Suspense>
  );
}
