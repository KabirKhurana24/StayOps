"use client";

import { ChevronRight, Menu as MenuIcon, Search } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Fragment } from "react";

import { Kbd } from "@/components/ui/misc";
import { RealtimeIndicator } from "@/features/realtime/realtime-indicator";

import { crumbsFor } from "./nav";
import { Notifications } from "./notifications";
import { useShell } from "./shell-context";
import { UserMenu } from "./user-menu";

export function Topbar() {
  const pathname = usePathname();
  const { setMobileNavOpen, setPaletteOpen, crumb } = useShell();
  const crumbs = crumbsFor(pathname);
  if (crumb && crumbs.length > 1) crumbs[crumbs.length - 1] = { label: crumb };

  return (
    <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center gap-2 border-b bg-background/85 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/70 sm:px-5">
      <button
        type="button"
        onClick={() => setMobileNavOpen(true)}
        className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground lg:hidden"
        aria-label="Open navigation"
      >
        <MenuIcon className="size-4" />
      </button>

      <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
        <ol className="flex min-w-0 items-center gap-1 text-[13px]">
          {crumbs.map((c, i) => (
            <Fragment key={`${c.label}-${i}`}>
              {i > 0 ? <ChevronRight className="size-3.5 shrink-0 text-subtle-foreground" aria-hidden /> : null}
              <li className={i === crumbs.length - 1 ? "min-w-0 truncate font-medium" : "shrink-0 text-muted-foreground"}>
                {c.href && i < crumbs.length - 1 ? (
                  <Link href={c.href} className="hover:text-foreground">
                    {c.label}
                  </Link>
                ) : (
                  <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{c.label}</span>
                )}
              </li>
            </Fragment>
          ))}
        </ol>
      </nav>

      <button
        type="button"
        onClick={() => setPaletteOpen(true)}
        className="hidden h-8 w-64 items-center gap-2 rounded-md border border-input bg-surface px-2.5 text-[13px] text-subtle-foreground shadow-xs transition-colors hover:border-border-strong md:flex"
        aria-label="Search (Ctrl+K)"
      >
        <Search className="size-3.5" aria-hidden />
        <span className="flex-1 text-left">Search…</span>
        <Kbd>Ctrl K</Kbd>
      </button>
      <button
        type="button"
        onClick={() => setPaletteOpen(true)}
        className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-muted md:hidden"
        aria-label="Search"
      >
        <Search className="size-4" />
      </button>

      <RealtimeIndicator />
      <Notifications />
      <div className="lg:hidden">
        <UserMenu />
      </div>
    </header>
  );
}
