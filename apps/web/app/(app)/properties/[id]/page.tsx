import type { Metadata } from "next";

import { PropertyDetail } from "@/features/properties/property-detail";

export const metadata: Metadata = { title: "Property" };

export default async function Page(props: PageProps<"/properties/[id]">) {
  const { id } = await props.params;
  return <PropertyDetail id={id} />;
}
