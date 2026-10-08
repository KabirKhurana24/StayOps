"use client";

import { AlertDialog, Dialog as RadixDialog } from "radix-ui";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const overlay =
  "fixed inset-0 z-50 bg-[rgb(20_24_28/0.28)] data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0";
const content =
  "fixed top-[12vh] left-1/2 z-50 w-[calc(100%-2rem)] -translate-x-1/2 rounded-xl border bg-surface shadow-popover duration-150 focus:outline-none data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-[0.98] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-[0.98]";

/** Centered modal (command palette, focused forms). */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  className,
  hideHeader = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
  hideHeader?: boolean;
}) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className={overlay} />
        <RadixDialog.Content className={cn(content, "max-w-lg", className)}>
          <div className={hideHeader ? "sr-only" : "border-b px-5 py-4"}>
            <RadixDialog.Title className="text-[15px] font-semibold">{title}</RadixDialog.Title>
            <RadixDialog.Description className={description ? "mt-0.5 text-xs text-muted-foreground" : "sr-only"}>
              {description ?? title}
            </RadixDialog.Description>
          </div>
          {children}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

/** Confirmation for destructive or irreversible actions (replaces window.confirm). */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel = "Keep",
  tone = "danger",
  pending = false,
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "danger" | "primary";
  pending?: boolean;
  onConfirm: () => void;
  children?: ReactNode;
}) {
  return (
    <AlertDialog.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className={overlay} />
        <AlertDialog.Content className={cn(content, "max-w-md p-5")}>
          <AlertDialog.Title className="text-[15px] font-semibold">{title}</AlertDialog.Title>
          <AlertDialog.Description asChild>
            <div className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">{description}</div>
          </AlertDialog.Description>
          {children}
          <div className="mt-5 flex justify-end gap-2">
            <AlertDialog.Cancel asChild>
              <Button disabled={pending}>{cancelLabel}</Button>
            </AlertDialog.Cancel>
            <Button
              variant={tone === "danger" ? "danger-solid" : "primary"}
              disabled={pending}
              onClick={(e) => {
                e.preventDefault();
                onConfirm();
              }}
            >
              {pending ? "Working…" : confirmLabel}
            </Button>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
