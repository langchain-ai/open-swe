import { writeStoredDesktopThreadSource } from "@/features/agents/lib/desktopThreadSource"
import { localRuntimeScript } from "@/lib/local-runtime"

export const DESKTOP_LOCAL_MODE_STORAGE_KEY =
  "open-swe.desktop.local-mode-without-sign-in"

/**
 * Whether this page is served by a local runtime — the desktop app, or an
 * `open-swe` server the user started themselves. Both can run local threads;
 * only a hosted deployment cannot.
 *
 * Read through the isomorphic value rather than the Electron bridge: the bridge
 * exists only on the client, so a render that consulted it disagreed with the
 * server's. The desktop window loads from its own local server, which answers
 * the same either way.
 */
export function isLocalRuntime(): boolean {
  return localRuntimeScript()
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
