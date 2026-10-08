import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/** Dense, readable data table primitives. Wrap in <TableScroll> for horizontal overflow. */
export function TableScroll({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("scrollbar-thin overflow-x-auto", className)} {...props} />;
}

export function Table({ className, ...props }: ComponentProps<"table">) {
  return <table className={cn("w-full border-collapse text-left text-[13px]", className)} {...props} />;
}

export function THead({ className, ...props }: ComponentProps<"thead">) {
  return <thead className={cn("border-b bg-surface-raised", className)} {...props} />;
}

export function Th({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      scope="col"
      className={cn("h-9 px-3 text-[11px] font-medium tracking-[0.04em] whitespace-nowrap text-muted-foreground uppercase first:pl-4 last:pr-4", className)}
      {...props}
    />
  );
}

export function Tr({ className, interactive = false, ...props }: ComponentProps<"tr"> & { interactive?: boolean }) {
  return (
    <tr
      className={cn(
        "border-b transition-colors duration-150 last:border-0",
        interactive && "cursor-pointer hover:bg-muted/45 focus-visible:bg-muted/45 focus-visible:outline-none",
        className,
      )}
      {...props}
    />
  );
}

export function Td({ className, ...props }: ComponentProps<"td">) {
  return <td className={cn("px-3 py-2.5 align-middle first:pl-4 last:pr-4", className)} {...props} />;
}
