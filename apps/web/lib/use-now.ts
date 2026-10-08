"use client";

import { useEffect, useState } from "react";

/**
 * The current time as epoch milliseconds, re-rendering every `intervalMs`.
 *
 * Render must stay pure (no `Date.now()` in the body), and relative labels such
 * as "overdue 5m" should keep moving while a board is open.
 */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
