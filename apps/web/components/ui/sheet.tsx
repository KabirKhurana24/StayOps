"use client";

import { X } from "lucide-react";
import { Dialog } from "radix-ui";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const WIDTH = { sm: "max-w-[380px]", md: "max-w-[480px]", lg: "max-w-[640px]", xl: "max-w-[760px]" } as const;

/** Side panel for create/edit/detail flows: keeps the operator's context visible behind it. */
export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  header,
  size = "md",
  side = "right",
  bodyClassName,
  hideTitle = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  /** Replaces the default title block (the title is still announced to screen readers). */
  header?: ReactNode;
  size?: keyof typeof WIDTH;
  side?: "right" | "left";
  bodyClassName?: string;
  hideTitle?: boolean;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-[rgb(20_24_28/0.24)] data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className={cn(
            "fixed inset-y-0 z-50 flex w-full flex-col bg-surface shadow-popover duration-200 focus:outline-none data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0",
            side === "right"
              ? "right-0 border-l data-[state=closed]:slide-out-to-right-6 data-[state=open]:slide-in-from-right-6"
              : "left-0 border-r data-[state=closed]:slide-out-to-left-6 data-[state=open]:slide-in-from-left-6",
            WIDTH[size],
          )}
        >
          {header ? (
            <>
              <Dialog.Title className="sr-only">{title}</Dialog.Title>
              <Dialog.Description className="sr-only">{description ?? title}</Dialog.Description>
              <div className="relative border-b">
                {header}
                <Dialog.Close className="absolute top-3 right-3 grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Close">
                  <X className="size-4" />
                </Dialog.Close>
              </div>
            </>
          ) : hideTitle ? (
            <>
              <Dialog.Title className="sr-only">{title}</Dialog.Title>
              <Dialog.Description className="sr-only">{description ?? title}</Dialog.Description>
              <Dialog.Close className="absolute top-3 right-3 z-10 grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Close">
                <X className="size-4" />
              </Dialog.Close>
            </>
          ) : (
            <div className="flex items-start justify-between gap-3 border-b px-5 py-4">
              <div className="min-w-0">
                <Dialog.Title className="text-[15px] font-semibold">{title}</Dialog.Title>
                <Dialog.Description className={description ? "mt-0.5 text-xs text-muted-foreground" : "sr-only"}>
                  {description ?? title}
                </Dialog.Description>
              </div>
              <Dialog.Close className="-mt-0.5 -mr-1.5 grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Close">
                <X className="size-4" />
              </Dialog.Close>
            </div>
          )}
          <div className={cn("scrollbar-thin flex-1 overflow-y-auto px-5 py-4", bodyClassName)}>{children}</div>
          {footer ? <div className="flex justify-end gap-2 border-t bg-surface-raised px-5 py-3">{footer}</div> : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
