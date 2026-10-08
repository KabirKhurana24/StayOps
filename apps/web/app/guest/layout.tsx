import type { Metadata } from "next";

import { PortalShell } from "@/features/guest-portal/portal-shell";

export const metadata: Metadata = {
  title: { default: "Your stay", template: "%s · Your stay" },
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

export default function GuestLayout({ children }: LayoutProps<"/guest">) {
  return <PortalShell>{children}</PortalShell>;
}
