"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronsUpDown } from "lucide-react";
import { useRouter } from "next/navigation";

import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/components/ui/menu";
import { Skeleton } from "@/components/ui/states";
import { useCurrentOrganization, useMe } from "@/features/organizations/api";
import { setActiveOrganizationId } from "@/lib/api/client";
import { cn } from "@/lib/utils";

function OrgMark({ name, className }: { name: string; className?: string }) {
  return (
    <span aria-hidden className={cn("grid size-7 shrink-0 place-items-center rounded-md bg-foreground text-[11px] font-semibold text-background", className)}>
      {name.trim().charAt(0).toUpperCase() || "?"}
    </span>
  );
}

/** Switches the active organization; every query refetches under the new tenant. */
export function OrgSwitcher({ collapsed = false }: { collapsed?: boolean }) {
  const org = useCurrentOrganization();
  const me = useMe();
  const qc = useQueryClient();
  const router = useRouter();
  const current = org.data?.organization;
  const memberships = me.data?.memberships ?? [];

  const switchTo = async (id: string) => {
    if (id === current?.id) return;
    setActiveOrganizationId(id);
    await qc.resetQueries(); // drop the previous tenant's data before anything renders
    router.push("/");
  };

  if (!current) {
    return (
      <div className={cn("flex items-center gap-2 px-2 py-1.5", collapsed && "justify-center px-0")}>
        <Skeleton className="size-7 rounded-md" />
        {!collapsed ? <Skeleton className="h-3 w-24" /> : null}
      </div>
    );
  }

  return (
    <Menu>
      <MenuTrigger
        className={cn(
          "group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted data-[state=open]:bg-muted",
          collapsed && "justify-center px-0",
        )}
        aria-label={`Organization: ${current.name}. Switch organization`}
      >
        <OrgMark name={current.name} />
        {!collapsed ? (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-semibold" data-testid="org-name">
                {current.name}
              </span>
              <span className="block truncate text-[11px] text-muted-foreground capitalize">{org.data?.role.toLowerCase()}</span>
            </span>
            <ChevronsUpDown className="size-3.5 shrink-0 text-subtle-foreground" />
          </>
        ) : (
          <span className="sr-only" data-testid="org-name">
            {current.name}
          </span>
        )}
      </MenuTrigger>
      <MenuContent align="start" className="w-64">
        <MenuLabel>Organizations</MenuLabel>
        {memberships.map((m) => (
          <MenuItem
            key={m.organization.id}
            onSelect={() => void switchTo(m.organization.id)}
            icon={<OrgMark name={m.organization.name} className="size-5 text-[10px]" />}
            shortcut={m.organization.id === current.id ? undefined : m.role.toLowerCase()}
          >
            <span className="flex items-center gap-2">
              {m.organization.name}
              {m.organization.id === current.id ? <Check className="size-3.5 text-accent" /> : null}
            </span>
          </MenuItem>
        ))}
      </MenuContent>
    </Menu>
  );
}
