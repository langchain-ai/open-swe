import { writeStoredDesktopThreadSource } from "@/features/agents/lib/desktopThreadSource"

export const DESKTOP_LOCAL_MODE_STORAGE_KEY =
  "open-swe.desktop.local-mode-without-sign-in"

export function isDesktopLocalModeEnabled(): boolean {
  return (
    typeof window !== "undefined" &&
    Boolean(window.openSweDesktop) &&
    window.localStorage.getItem(DESKTOP_LOCAL_MODE_STORAGE_KEY) === "true"
  )
}

export function enableDesktopLocalMode(): void {
  if (typeof window === "undefined" || !window.openSweDesktop) return
  window.localStorage.setItem(DESKTOP_LOCAL_MODE_STORAGE_KEY, "true")
  writeStoredDesktopThreadSource("local")
}
