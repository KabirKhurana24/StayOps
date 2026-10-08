"use client";

import { useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { Sheet } from "@/components/ui/sheet";
import { TooltipProvider } from "@/components/ui/menu";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { useCurrentOrganization } from "@/features/organizations/api";
import { RealtimeProvider } from "@/features/realtime/realtime-provider";
import { ApiError, setActiveOrganizationId } from "@/lib/api/client";

import { CommandPalette } from "./command-palette";
import { ShellProvider, useShell } from "./shell-context";
import { Sidebar, SidebarContent } from "./sidebar";
import { Topbar } from "./topbar";

function MobileNav() {
  const { mobileNavOpen, setMobileNavOpen } = useShell();
  return (
    <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen} title="Navigation" side="left" size="sm" hideTitle bodyClassName="p-0">
      <SidebarContent showCollapse={false} onNavigate={() => setMobileNavOpen(false)} />
    </Sheet>
  );
}

function ShellBody({ children }: { children: ReactNode }) {
  const router = useRouter();
  const org = useCurrentOrganization();

  const noOrganization = org.error instanceof ApiError && org.error.code === "no_organization";
  const foreignOrganization = org.error instanceof ApiError && org.error.code === "forbidden";

  useEffect(() => {
    if (noOrganization) router.replace("/onboarding");
    if (foreignOrganization) {
      // Stale org preference (e.g. membership removed): fall back to the default.
      setActiveOrganizationId(null);
      void org.refetch();
    }
  }, [noOrganization, foreignOrganization, router, org]);

  return (
    <RealtimeProvider topic={org.data?.realtime_topic}>
      <div className="flex min-h-screen">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar />
          <main id="main" className="mx-auto w-full max-w-[1600px] min-w-0 flex-1 px-4 py-5 sm:px-6 lg:px-8 lg:py-6">
            {org.isPending || noOrganization ? (
              <div className="space-y-4" aria-busy="true" aria-label="Loading">
                <Skeleton className="h-6 w-56" />
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-64 w-full" />
              </div>
            ) : org.isError && !foreignOrganization ? (
              <ErrorState error={org.error} onRetry={() => org.refetch()} />
            ) : (
              <div className="animate-fade-up">{children}</div>
            )}
          </main>
        </div>
      </div>
      <MobileNav />
      <CommandPalette />
    </RealtimeProvider>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <ShellProvider>
      <TooltipProvider delayDuration={300} skipDelayDuration={150}>
        <a href="#main" className="sr-only z-50 rounded-md bg-surface px-3 py-2 focus:not-sr-only focus:fixed focus:top-2 focus:left-2">
          Skip to content
        </a>
        <ShellBody>{children}</ShellBody>
      </TooltipProvider>
    </ShellProvider>
  );
}
