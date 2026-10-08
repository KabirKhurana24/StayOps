"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ChevronsUpDown, LogOut, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";

import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Avatar } from "@/components/ui/misc";
import { useCurrentOrganization, useMe } from "@/features/organizations/api";
import { setActiveOrganizationId } from "@/lib/api/client";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";

export function useSignOut() {
  const router = useRouter();
  const qc = useQueryClient();
  return async () => {
    await getSupabaseBrowserClient().auth.signOut();
    setActiveOrganizationId(null);
    qc.clear();
    router.replace("/login");
    router.refresh();
  };
}

/** Account menu. `variant="sidebar"` renders the full user row; `"avatar"` a compact trigger. */
export function UserMenu({ variant = "avatar", collapsed = false }: { variant?: "avatar" | "sidebar"; collapsed?: boolean }) {
  const me = useMe();
  const org = useCurrentOrganization();
  const signOut = useSignOut();
  const name = me.data?.full_name || me.data?.email || "Account";

  return (
    <Menu>
      {variant === "sidebar" ? (
        <MenuTrigger
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted data-[state=open]:bg-muted",
            collapsed && "justify-center px-0",
          )}
          aria-label="Account menu"
        >
          <Avatar name={name} size="sm" />
          {!collapsed ? (
            <>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium">{name}</span>
                <span className="block truncate text-[11px] text-muted-foreground">{me.data?.email}</span>
              </span>
              <ChevronsUpDown className="size-3.5 text-subtle-foreground" />
            </>
          ) : null}
        </MenuTrigger>
      ) : (
        <MenuTrigger className="rounded-full focus-visible:outline-offset-1" aria-label="Account menu">
          <Avatar name={name} size="sm" />
        </MenuTrigger>
      )}
      <MenuContent align={variant === "sidebar" ? "start" : "end"} side={variant === "sidebar" ? "top" : "bottom"} className="w-60">
        <MenuLabel className="pb-1.5">
          <span className="block truncate text-[13px] font-medium text-foreground">{name}</span>
          <span className="block truncate font-normal">{me.data?.email}</span>
        </MenuLabel>
        <MenuSeparator />
        <MenuItem icon={<UserRound />} disabled shortcut={org.data?.role.toLowerCase()}>
          Profile
        </MenuItem>
        <MenuSeparator />
        <MenuItem icon={<LogOut />} onSelect={() => void signOut()}>
          Sign out
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}
