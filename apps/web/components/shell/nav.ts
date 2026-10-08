import {
  Activity,
  BarChart3,
  BedDouble,
  Building2,
  CalendarDays,
  Inbox,
  LayoutDashboard,
  ListChecks,
  type LucideIcon,
  Settings,
  Sparkles,
  Users,
  Wallet,
  Workflow,
  Wrench,
} from "lucide-react";

export type NavItem =
  | { label: string; icon: LucideIcon; href: string; exact?: boolean; soon?: false }
  | { label: string; icon: LucideIcon; soon: true; description: string };

export type NavSection = { label: string; items: NavItem[] };

/**
 * Product navigation. Modules that are not built yet are listed (so the product's
 * shape is visible) but rendered disabled — they never link to placeholder screens.
 */
export const NAV_SECTIONS: NavSection[] = [
  {
    label: "Operations",
    items: [
      { label: "Command Center", icon: LayoutDashboard, href: "/", exact: true },
      { label: "Reservations", icon: BedDouble, href: "/reservations" },
      { label: "Calendar", icon: CalendarDays, href: "/calendar" },
      { label: "Properties", icon: Building2, href: "/properties" },
      { label: "Tasks", icon: ListChecks, href: "/tasks" },
      { label: "Cleaning", icon: Sparkles, href: "/cleaning" },
      { label: "Maintenance", icon: Wrench, href: "/maintenance" },
    ],
  },
  {
    label: "People",
    items: [
      { label: "Guests", icon: Users, href: "/guests" },
      { label: "Inbox", icon: Inbox, soon: true, description: "Unified guest messaging" },
    ],
  },
  {
    label: "Business",
    items: [
      { label: "Analytics", icon: BarChart3, soon: true, description: "Occupancy, ADR and RevPAR" },
      { label: "Finance", icon: Wallet, soon: true, description: "Payments, payouts and owner statements" },
    ],
  },
  {
    label: "System",
    items: [
      { label: "Activity", icon: Activity, href: "/activity" },
      { label: "Automations", icon: Workflow, soon: true, description: "Event-driven workflows" },
      { label: "Settings", icon: Settings, soon: true, description: "Organization, team and integrations" },
    ],
  },
];

export const LIVE_NAV = NAV_SECTIONS.flatMap((s) => s.items).filter(
  (i): i is Extract<NavItem, { href: string }> => "href" in i,
);

export function isActive(pathname: string, item: { href: string; exact?: boolean }): boolean {
  return item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

/** Breadcrumb trail derived from the route; detail pages can override the last label. */
export function crumbsFor(pathname: string): { label: string; href?: string }[] {
  const section = LIVE_NAV.find((i) => i.href !== "/" && pathname.startsWith(i.href));
  if (!section) return [{ label: "Command Center" }];
  const rest = pathname.slice(section.href.length).replace(/^\//, "");
  if (!rest) return [{ label: section.label }];
  const singular: Record<string, string> = {
    Reservations: "Reservation",
    Properties: "Property",
    Guests: "Guest",
    Tasks: "Task",
    Maintenance: "Work order",
  };
  return [{ label: section.label, href: section.href }, { label: singular[section.label] ?? "Detail" }];
}
