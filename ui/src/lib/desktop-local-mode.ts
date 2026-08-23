import { writeStoredDesktopThreadSource } from "@/features/agents/lib/desktopThreadSource"

export const DESKTOP_LOCAL_MODE_STORAGE_KEY =
  "open-swe.desktop.local-mode-without-sign-in"

/**
 * Whether this page is served by a local runtime — the desktop app, or an
 * `open-swe` server the user started themselves. Both can run local threads;
 * only a hosted deployment cannot.
 */
export function isLocalRuntime(): boolean {
  if (typeof window === "undefined") return false
  return Boolean(window.openSweDesktop) || window.__OPEN_SWE_LOCAL__ === true
}

export function isDesktopLocalModeEnabled(): boolean {
  return (
    isLocalRuntime() &&
    window.localStorage.getItem(DESKTOP_LOCAL_MODE_STORAGE_KEY) === "true"
  )
}

export function enableDesktopLocalMode(): void {
  if (!isLocalRuntime()) return
  window.localStorage.setItem(DESKTOP_LOCAL_MODE_STORAGE_KEY, "true")
  writeStoredDesktopThreadSource("local")
}
