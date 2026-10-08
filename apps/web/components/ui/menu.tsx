"use client";

import { Check } from "lucide-react";
import { DropdownMenu, Popover as RadixPopover, Tooltip as RadixTooltip } from "radix-ui";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

const surface =
  "z-50 min-w-[180px] overflow-hidden rounded-lg border bg-surface p-1 text-[13px] shadow-popover data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[side=bottom]:slide-in-from-top-1 data-[side=top]:slide-in-from-bottom-1";

export const Menu = DropdownMenu.Root;
export const MenuTrigger = DropdownMenu.Trigger;
export const MenuGroup = DropdownMenu.Group;

export function MenuContent({ className, align = "end", sideOffset = 6, ...props }: ComponentProps<typeof DropdownMenu.Content>) {
  return (
    <DropdownMenu.Portal>
      <DropdownMenu.Content align={align} sideOffset={sideOffset} className={cn(surface, className)} {...props} />
    </DropdownMenu.Portal>
  );
}

export function MenuItem({
  className,
  icon,
  shortcut,
  tone,
  children,
  asChild,
  ...props
}: ComponentProps<typeof DropdownMenu.Item> & { icon?: ReactNode; shortcut?: string; tone?: "danger" }) {
  const classes = cn(
    "flex h-8 cursor-default items-center gap-2 rounded-md px-2 outline-none select-none data-[disabled]:pointer-events-none data-[disabled]:opacity-45 data-[highlighted]:bg-muted [&_svg]:size-3.5 [&_svg]:text-muted-foreground",
    tone === "danger" && "text-danger data-[highlighted]:bg-danger/6 [&_svg]:text-danger",
    className,
  );
  // asChild (e.g. a <Link>): Radix Slot needs exactly one child, so the caller
  // renders the icon/label inside that element.
  if (asChild) {
    return (
      <DropdownMenu.Item asChild className={classes} {...props}>
        {children}
      </DropdownMenu.Item>
    );
  }
  return (
    <DropdownMenu.Item className={classes} {...props}>
      {icon}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {shortcut ? <span className="text-[11px] text-subtle-foreground">{shortcut}</span> : null}
    </DropdownMenu.Item>
  );
}

export function MenuCheckboxItem({ className, children, ...props }: ComponentProps<typeof DropdownMenu.CheckboxItem>) {
  return (
    <DropdownMenu.CheckboxItem
      className={cn("flex h-8 cursor-default items-center gap-2 rounded-md px-2 outline-none select-none data-[highlighted]:bg-muted", className)}
      onSelect={(e) => e.preventDefault()}
      {...props}
    >
      <span className="grid size-4 place-items-center rounded-[4px] border border-border-strong data-[state=checked]:border-accent data-[state=checked]:bg-accent">
        <DropdownMenu.ItemIndicator>
          <Check className="size-3 text-white" strokeWidth={3} />
        </DropdownMenu.ItemIndicator>
      </span>
      <span className="flex-1">{children}</span>
    </DropdownMenu.CheckboxItem>
  );
}

export function MenuLabel({ className, ...props }: ComponentProps<typeof DropdownMenu.Label>) {
  return <DropdownMenu.Label className={cn("px-2 pt-1.5 pb-1 text-[11px] font-medium text-subtle-foreground", className)} {...props} />;
}

export function MenuSeparator({ className, ...props }: ComponentProps<typeof DropdownMenu.Separator>) {
  return <DropdownMenu.Separator className={cn("-mx-1 my-1 h-px bg-border", className)} {...props} />;
}

export const Popover = RadixPopover.Root;
export const PopoverTrigger = RadixPopover.Trigger;

export function PopoverContent({ className, align = "end", sideOffset = 6, ...props }: ComponentProps<typeof RadixPopover.Content>) {
  return (
    <RadixPopover.Portal>
      <RadixPopover.Content align={align} sideOffset={sideOffset} className={cn(surface, "p-0", className)} {...props} />
    </RadixPopover.Portal>
  );
}

export const TooltipProvider = RadixTooltip.Provider;

export function Tooltip({ content, children, side = "right", disabled = false }: { content: ReactNode; children: ReactNode; side?: "top" | "right" | "bottom" | "left"; disabled?: boolean }) {
  if (disabled) return <>{children}</>;
  return (
    <RadixTooltip.Root>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          side={side}
          sideOffset={8}
          className="z-50 rounded-md bg-foreground px-2 py-1 text-xs text-background shadow-popover data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0"
        >
          {content}
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}
