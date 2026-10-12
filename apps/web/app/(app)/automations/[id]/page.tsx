import type { Metadata } from "next";
import { Suspense } from "react";

import { SkeletonRows } from "@/components/ui/states";
import { WorkflowEditor } from "@/features/automations/editor/workflow-editor";

export const metadata: Metadata = { title: "Workflow" };

export default async function Page(props: PageProps<"/automations/[id]">) {
  const { id } = await props.params;
  return (
    <Suspense fallback={<SkeletonRows rows={6} />}>
      <WorkflowEditor id={id} />
    </Suspense>
  );
}
