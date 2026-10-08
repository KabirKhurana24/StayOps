import type { Metadata } from "next";

import { CommandCenterView } from "@/features/command-center/command-center";

export const metadata: Metadata = { title: "Command Center" };

export default function CommandCenterPage() {
  return <CommandCenterView />;
}
