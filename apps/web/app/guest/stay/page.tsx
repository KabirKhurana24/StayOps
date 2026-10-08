import type { Metadata } from "next";

import { GuestGuide } from "@/features/guest-portal/guest-guide";

export const metadata: Metadata = { title: "House guide" };

export default function Page() {
  return <GuestGuide />;
}
