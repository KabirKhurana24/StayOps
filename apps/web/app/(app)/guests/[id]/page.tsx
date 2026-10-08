import type { Metadata } from "next";

import { GuestDetail } from "@/features/guests/guest-detail";

export const metadata: Metadata = { title: "Guest" };

export default async function GuestPage(props: PageProps<"/guests/[id]">) {
  const { id } = await props.params;
  return <GuestDetail id={id} />;
}
