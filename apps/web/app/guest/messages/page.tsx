import type { Metadata } from "next";

import { GuestMessages } from "@/features/guest-portal/guest-messages";

export const metadata: Metadata = { title: "Messages" };

export default function Page() {
  return <GuestMessages />;
}
