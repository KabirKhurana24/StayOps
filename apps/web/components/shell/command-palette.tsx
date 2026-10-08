"use client";

import { CornerDownLeft, Plus, Search, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { type ReactNode, useDeferredValue, useState } from "react";

import { Dialog } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/misc";
import { useGuests } from "@/features/guests/api";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { useReservations } from "@/features/reservations/api";
import { StatusBadge } from "@/features/reservations/components/status-badge";
import { formatStayRange, guestName } from "@/lib/format";
import { cn } from "@/lib/utils";

import { LIVE_NAV } from "./nav";
import { useShell } from "./shell-context";

type Item = { id: string; group: string; label: ReactNode; hint?: ReactNode; icon: ReactNode; run: () => void; keywords: string };

/** ⌘K: jump to a screen, start an action, or find a reservation or guest. */
export function CommandPalette() {
  const { paletteOpen, setPaletteOpen } = useShell();
  return (
    <Dialog open={paletteOpen} onOpenChange={setPaletteOpen} title="Search StayOps" hideHeader className="max-w-xl overflow-hidden">
      {paletteOpen ? <PaletteBody close={() => setPaletteOpen(false)} /> : null}
    </Dialog>
  );
}

function PaletteBody({ close }: { close: () => void }) {
  const router = useRouter();
  const org = useCurrentOrganization();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const q = useDeferredValue(query.trim());
  const searching = q.length >= 2;

  const reservations = useReservations({ q: searching ? q : undefined, limit: 6, sort: "-check_in" }, { enabled: searching });
  const guests = useGuests(searching ? q : "", 5, { stats: false });

  const go = (href: string) => {
    close();
    router.push(href);
  };

  const items: Item[] = (() => {
    const lower = query.trim().toLowerCase();
    const nav: Item[] = LIVE_NAV.map((n) => ({
      id: `nav:${n.href}`,
      group: "Go to",
      label: n.label,
      icon: <n.icon className="size-4" />,
      run: () => go(n.href),
      keywords: n.label.toLowerCase(),
    }));
    const actions: Item[] = can(org.data, "reservations:write")
      ? [
          {
            id: "action:new-reservation",
            group: "Actions",
            label: "New reservation",
            icon: <Plus className="size-4" />,
            run: () => go("/reservations?new=1"),
            keywords: "new create reservation booking",
          },
        ]
      : [];
    const filtered = [...actions, ...nav].filter((i) => !lower || i.keywords.includes(lower));
    if (!searching) return filtered;
    const res: Item[] = (reservations.data?.items ?? []).map((r) => ({
      id: `res:${r.id}`,
      group: "Reservations",
      label: (
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate">{guestName(r.guest)}</span>
          <span className="font-mono text-[11px] text-subtle-foreground">{r.confirmation_code}</span>
        </span>
      ),
      hint: (
        <span className="flex items-center gap-2">
          <span className="hidden sm:inline">
            {r.unit.name} · {formatStayRange(r.check_in, r.check_out, r.property.timezone)}
          </span>
          <StatusBadge status={r.status} />
        </span>
      ),
      icon: <CornerDownLeft className="size-4" />,
      run: () => go(`/reservations?r=${r.id}`),
      keywords: "",
    }));
    const gs: Item[] = (guests.data?.items ?? []).map((g) => ({
      id: `guest:${g.id}`,
      group: "Guests",
      label: guestName(g),
      hint: g.email,
      icon: <UserRound className="size-4" />,
      run: () => go(`/guests/${g.id}`),
      keywords: "",
    }));
    return [...filtered, ...res, ...gs];
  })();

  const clamped = Math.min(active, Math.max(items.length - 1, 0));
  const groups = items.reduce<Record<string, Item[]>>((acc, item) => {
    (acc[item.group] ??= []).push(item);
    return acc;
  }, {});

  return (
    <div
      onKeyDown={(e) => {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setActive((clamped + 1) % Math.max(items.length, 1));
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          setActive((clamped - 1 + items.length) % Math.max(items.length, 1));
        } else if (e.key === "Enter") {
          e.preventDefault();
          items[clamped]?.run();
        }
      }}
    >
      <div className="flex items-center gap-2.5 border-b px-4">
        <Search className="size-4 text-subtle-foreground" aria-hidden />
        <input
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          placeholder="Search reservations, guests, or jump to…"
          aria-label="Search"
          role="combobox"
          aria-expanded
          aria-controls="palette-results"
          aria-activedescendant={items[clamped]?.id}
          className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-subtle-foreground"
        />
        <Kbd>Esc</Kbd>
      </div>
      <div id="palette-results" role="listbox" className="scrollbar-thin max-h-[min(420px,60vh)] overflow-y-auto p-1.5">
        {items.length === 0 ? (
          <p className="px-3 py-8 text-center text-[13px] text-muted-foreground">
            {searching && (reservations.isFetching || guests.isFetching) ? "Searching…" : "No matches"}
          </p>
        ) : (
          Object.entries(groups).map(([group, list]) => (
            <div key={group} className="pb-1">
              <div className="px-2.5 pt-2 pb-1 text-[11px] font-medium text-subtle-foreground">{group}</div>
              {list.map((item) => {
                const index = items.indexOf(item);
                return (
                  <button
                    key={item.id}
                    id={item.id}
                    type="button"
                    role="option"
                    aria-selected={index === clamped}
                    onMouseMove={() => setActive(index)}
                    onClick={item.run}
                    className={cn(
                      "flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-[13px] text-foreground",
                      index === clamped && "bg-muted",
                    )}
                  >
                    <span className="text-subtle-foreground">{item.icon}</span>
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    {item.hint ? <span className="shrink-0 text-xs text-muted-foreground">{item.hint}</span> : null}
                  </button>
                );
              })}
            </div>
          ))
        )}
      </div>
      <div className="flex items-center gap-3 border-t bg-surface-raised px-4 py-2 text-[11px] text-subtle-foreground">
        <span className="flex items-center gap-1">
          <Kbd>↑</Kbd>
          <Kbd>↓</Kbd> navigate
        </span>
        <span className="flex items-center gap-1">
          <Kbd>↵</Kbd> open
        </span>
      </div>
    </div>
  );
}
