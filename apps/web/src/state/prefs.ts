import { useEffect, useState, useSyncExternalStore } from "react";

import { backendMode } from "../api/client.ts";

export type Theme = "dark" | "light";

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme === "light" ? "light" : "dark"));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("r3.theme", theme); } catch { /* private mode */ }
    window.dispatchEvent(new CustomEvent("r3-theme", { detail: theme }));
  }, [theme]);
  return [theme, setTheme];
}

/** Re-renders when the theme attribute changes (charts and the 3D view read CSS variables). */
export function useThemeVersion(): string {
  return useSyncExternalStore(
    (notify) => {
      const observer = new MutationObserver(notify);
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      return () => observer.disconnect();
    },
    () => document.documentElement.dataset.theme ?? "dark",
  );
}

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** true when the dev mock layer answers instead of `r3 serve`; null until the first response. */
export function useMockBackend(): boolean | null {
  return useSyncExternalStore(
    (notify) => { backendMode.listeners.add(notify); return () => backendMode.listeners.delete(notify); },
    () => backendMode.mock,
  );
}
