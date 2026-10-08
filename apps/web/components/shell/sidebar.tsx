"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Tooltip } from "@/components/ui/menu";
import { cn } from "@/lib/utils";

import { isActive, NAV_SECTIONS, type NavItem } from "./nav";
import { OrgSwitcher } from "./org-switcher";
import { useShell } from "./shell-context";
import { UserMenu } from "./user-menu";

function Wordmark({ collapsed }: { collapsed: boolean }) {
  return (
    <Link href="/" className="flex items-center gap-2 rounded-md px-1 py-0.5" aria-label="StayOps home">
      <span aria-hidden className="grid size-6 place-items-center rounded-[6px] bg-primary text-primary-foreground">
        <svg viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 7.5 8 3.5l5 4V13H3z" />
          <path d="M6.5 13V9.5h3V13" />
        </svg>
      </span>
      {!collapsed ? <span className="text-[13px] font-bold tracking-[0.16em]">STAYOPS</span> : null}
    </Link>
  );
}

function NavLink({ item, collapsed, onNavigate }: { item: NavItem; collapsed: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const Icon = item.icon;
  const base = "group flex h-8 items-center gap-2.5 rounded-md px-2 text-[13px] transition-colors duration-150";

  if (item.soon) {
    return (
      <Tooltip content={`${item.label} · ${item.description} — coming soon`} side="right">
        <span aria-disabled="true" className={cn(base, "cursor-not-allowed text-subtle-foreground/80", collapsed && "justify-center px-0")}>
          <Icon className="size-4 shrink-0 opacity-70" aria-hidden />
          {!collapsed ? (
            <>
              <span className="flex-1 truncate">{item.label}</span>
              <span className="rounded-[4px] border border-border px-1 text-[10px] leading-4 text-subtle-foreground">Soon</span>
            </>
          ) : (
            <span className="sr-only">{item.label} (coming soon)</span>
          )}
        </span>
      </Tooltip>
    );
  }

  const active = isActive(pathname, item);
  return (
    <Tooltip content={item.label} side="right" disabled={!collapsed}>
      <Link
        href={item.href}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        className={cn(
          base,
          active ? "bg-surface font-medium text-foreground shadow-sm ring-1 ring-border" : "text-muted-foreground hover:bg-muted hover:text-foreground",
          collapsed && "justify-center px-0",
        )}
      >
        <Icon className={cn("size-4 shrink-0", active ? "text-accent" : "text-subtle-foreground group-hover:text-muted-foreground")} aria-hidden />
        {!collapsed ? <span className="truncate">{item.label}</span> : <span className="sr-only">{item.label}</span>}
      </Link>
    </Tooltip>
  );
}

/** Sidebar contents (shared by the desktop rail and the mobile drawer). */
export function SidebarContent({ collapsed = false, onNavigate, showCollapse = true }: { collapsed?: boolean; onNavigate?: () => void; showCollapse?: boolean }) {
  const { toggleCollapsed } = useShell();
  return (
    <div className="flex h-full flex-col">
      <div className={cn("flex h-12 items-center justify-between gap-2 px-3", collapsed && "justify-center px-0")}>
        <Wordmark collapsed={collapsed} />
        {showCollapse && !collapsed ? (
          <button
            type="button"
            onClick={toggleCollapsed}
            className="grid size-7 place-items-center rounded-md text-subtle-foreground hover:bg-muted hover:text-foreground"
            aria-label="Collapse sidebar"
          >
            <PanelLeftClose className="size-4" />
          </button>
        ) : null}
      </div>
      <div className={cn("px-2 pb-2", collapsed && "px-1.5")}>
        <OrgSwitcher collapsed={collapsed} />
      </div>
      <nav aria-label="Main" className={cn("scrollbar-thin flex-1 space-y-4 overflow-y-auto px-2 py-2", collapsed && "px-1.5")}>
        {NAV_SECTIONS.map((section) => (
          <div key={section.label}>
            {!collapsed ? (
              <div className="px-2 pb-1 text-[11px] font-medium text-subtle-foreground">{section.label}</div>
            ) : (
              <div className="mx-auto mb-1 h-px w-5 bg-border" aria-hidden />
            )}
            <ul className="space-y-px">
              {section.items.map((item) => (
                <li key={item.label}>
                  <NavLink item={item} collapsed={collapsed} onNavigate={onNavigate} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
      <div className={cn("border-t p-2", collapsed && "px-1.5")}>
        {showCollapse && collapsed ? (
          <button
            type="button"
            onClick={toggleCollapsed}
            className="mb-1 grid h-8 w-full place-items-center rounded-md text-subtle-foreground hover:bg-muted hover:text-foreground"
            aria-label="Expand sidebar"
          >
            <PanelLeftOpen className="size-4" />
          </button>
        ) : null}
        <UserMenu variant="sidebar" collapsed={collapsed} />
      </div>
    </div>
  );
}

export function Sidebar() {
  const { collapsed } = useShell();
  return (
    <aside
      className={cn(
        "sticky top-0 hidden h-screen shrink-0 border-r bg-sidebar transition-[width] duration-200 ease-out lg:block",
        collapsed ? "w-[60px]" : "w-[232px]",
      )}
    >
      <SidebarContent collapsed={collapsed} />
    </aside>
  );
}
