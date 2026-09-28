import type { DesktopBrowserBridge } from "@/desktop"

/** The Electron preload's browser namespace, or null on the web build. */
export function readDesktopBrowserBridge(): DesktopBrowserBridge | null {
  if (typeof window === "undefined") return null
  return window.openSweDesktop?.browser ?? null
}

export function isDesktopBrowserAvailable(): boolean {
  return readDesktopBrowserBridge() !== null
}
