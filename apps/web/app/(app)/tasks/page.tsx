import type { Metadata } from "next";
import { Suspense } from "react";

import { SkeletonRows } from "@/components/ui/states";
import { TasksPage } from "@/features/tasks/tasks-page";

export const metadata: Metadata = { title: "Tasks" };

export default function Page() {
  return (
    <Suspense fallback={<SkeletonRows rows={10} />}>
      <TasksPage />
    </Suspense>
  );
}
