"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useCallback } from "react";

/**
 * Read/write URL search params without a server round-trip. Next.js syncs
 * `window.history` updates with `useSearchParams`, so filters and open drawers
 * are shareable and survive reloads while staying instant.
 */
export function useSearchState() {
  const params = useSearchParams();
  const pathname = usePathname();

  const set = useCallback(
    (updates: Record<string, string | null | undefined>, { push = false }: { push?: boolean } = {}) => {
      const next = new URLSearchParams(window.location.search);
      for (const [key, value] of Object.entries(updates)) {
        if (value === null || value === undefined || value === "") next.delete(key);
        else next.set(key, value);
      }
      const qs = next.toString();
      const url = qs ? `${pathname}?${qs}` : pathname;
      if (push) window.history.pushState(null, "", url);
      else window.history.replaceState(null, "", url);
    },
    [pathname],
  );

  return { params, get: (key: string) => params.get(key) ?? "", set };
}
