import type { Metadata } from "next";

import { GuestsPage } from "@/features/guests/guests-page";

export const metadata: Metadata = { title: "Guests" };

export default function Page() {
  return <GuestsPage />;
}
