"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";

type ShellState = {
  collapsed: boolean;
  toggleCollapsed: () => void;
  mobileNavOpen: boolean;
  setMobileNavOpen: (open: boolean) => void;
  paletteOpen: boolean;
  setPaletteOpen: (open: boolean) => void;
  crumb: string | null;
  setCrumb: (label: string | null) => void;
};

const ShellContext = createContext<ShellState | null>(null);
const COLLAPSE_KEY = "stayops.sidebarCollapsed"; // UI preference only
const COLLAPSE_EVENT = "stayops:sidebar";

function subscribeCollapsed(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(COLLAPSE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(COLLAPSE_EVENT, onChange);
  };
}
const readCollapsed = () => window.localStorage.getItem(COLLAPSE_KEY) === "1";
const serverCollapsed = () => false;

export function ShellProvider({ children }: { children: ReactNode }) {
  const collapsed = useSyncExternalStore(subscribeCollapsed, readCollapsed, serverCollapsed);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [crumb, setCrumb] = useState<string | null>(null);

  const toggleCollapsed = useCallback(() => {
    window.localStorage.setItem(COLLAPSE_KEY, readCollapsed() ? "0" : "1");
    window.dispatchEvent(new Event(COLLAPSE_EVENT));
  }, []);

  // ⌘K / Ctrl+K opens the command palette from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const value = useMemo(
    () => ({ collapsed, toggleCollapsed, mobileNavOpen, setMobileNavOpen, paletteOpen, setPaletteOpen, crumb, setCrumb }),
    [collapsed, toggleCollapsed, mobileNavOpen, paletteOpen, crumb],
  );
  return <ShellContext.Provider value={value}>{children}</ShellContext.Provider>;
}

export function useShell(): ShellState {
  const ctx = useContext(ShellContext);
  if (!ctx) throw new Error("useShell must be used inside <ShellProvider>");
  return ctx;
}

/** Detail pages set the last breadcrumb label (e.g. a property's name). */
export function useBreadcrumb(label: string | null | undefined) {
  const { setCrumb } = useShell();
  useEffect(() => {
    setCrumb(label ?? null);
    return () => setCrumb(null);
  }, [label, setCrumb]);
}
