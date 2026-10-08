"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BookOpen, ClipboardList, Home, KeyRound, MessageCircle } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useState, useSyncExternalStore } from "react";

import { ApiError } from "@/lib/api/client";
import { getGuestToken, setGuestToken } from "@/lib/api/guest-client";
import { cn } from "@/lib/utils";

import { useGuestStay } from "./api";
import { isPlausibleToken, tokenFromHash } from "./token";

const TABS = [
  { href: "/guest", label: "Stay", icon: Home, exact: true },
  { href: "/guest/stay", label: "Guide", icon: BookOpen },
  { href: "/guest/messages", label: "Messages", icon: MessageCircle },
  { href: "/guest/requests", label: "Requests", icon: ClipboardList },
] as const;

function NoAccess({ message }: { message: string }) {
  return (
    <div className="grid min-h-dvh place-items-center bg-background px-6">
      <div className="max-w-sm text-center" data-testid="guest-no-access">
        <span className="mx-auto mb-4 grid size-12 place-items-center rounded-full bg-muted text-muted-foreground">
          <KeyRound className="size-5" />
        </span>
        <h1 className="text-lg font-semibold">Your stay portal</h1>
        <p className="mt-2 text-sm text-muted-foreground">{message}</p>
      </div>
    </div>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const stay = useGuestStay();

  if (stay.error instanceof ApiError && (stay.error.status === 401 || stay.error.status === 403)) {
    return <NoAccess message={stay.error.message} />;
  }

  const unread = stay.data?.unread_messages ?? 0;
  return (
    <div className="mx-auto flex min-h-dvh max-w-[560px] flex-col bg-background" data-testid="guest-portal">
      <header className="sticky top-0 z-20 flex items-center justify-between border-b bg-surface/95 px-4 py-3 backdrop-blur">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-semibold">{stay.data?.host_name ?? " "}</p>
          <p className="truncate text-xs text-muted-foreground">{stay.data ? `${stay.data.property.name} · ${stay.data.unit_name}` : " "}</p>
        </div>
        {stay.data ? <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium tabular">{stay.data.reservation.confirmation_code}</span> : null}
      </header>
      <main className="flex-1 px-4 pt-4 pb-24">{children}</main>
      <nav aria-label="Portal" className="fixed inset-x-0 bottom-0 z-20 border-t bg-surface/95 backdrop-blur pb-[env(safe-area-inset-bottom)]">
        <ul className="mx-auto grid max-w-[560px] grid-cols-4">
          {TABS.map(({ href, label, icon: Icon, ...rest }) => {
            const active = "exact" in rest ? pathname === href : pathname.startsWith(href);
            return (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cn("relative flex flex-col items-center gap-0.5 py-2.5 text-[11px] font-medium", active ? "text-accent" : "text-muted-foreground")}
                >
                  <Icon className="size-5" aria-hidden />
                  {label}
                  {label === "Messages" && unread > 0 ? (
                    <span className="absolute top-1.5 left-1/2 ml-2 grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] text-white" aria-label={`${unread} unread`}>
                      {unread}
                    </span>
                  ) : null}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}

const noSubscription = () => () => {};

/**
 * Moves a token arriving in the URL fragment into storage (idempotent: the fragment
 * is stripped on the first read), then reports whether the portal can be used.
 */
function readAccess(): "ready" | "missing" {
  const fromHash = tokenFromHash(window.location.hash);
  if (fromHash) {
    setGuestToken(fromHash);
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  return isPlausibleToken(getGuestToken()) ? "ready" : "missing";
}

/**
 * Guest portal root. Uses its own query client: guest calls must never trigger the
 * operator app's "session expired → /login" handling.
 */
export function PortalShell({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
          },
        },
      }),
  );
  // Server render: "checking" (no storage). Client: the stored/just-arrived token.
  const state = useSyncExternalStore(noSubscription, readAccess, () => "checking" as const);

  if (state === "checking") return <div className="min-h-dvh bg-background" aria-busy="true" />;
  if (state === "missing") return <NoAccess message="Open the guest portal from the link your host sent you." />;
  return (
    <QueryClientProvider client={client}>
      <Frame>{children}</Frame>
    </QueryClientProvider>
  );
}
