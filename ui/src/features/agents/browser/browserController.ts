/**
 * Imperative handle for one browser tab. Each host (the desktop webview and
 * the cloud CDP session) registers a controller for the tabs it renders; the
 * panel chrome talks to whichever one is current and never needs to know how
 * the page is hosted.
 */
import type { PanelThreadRef } from "@/features/agents/lib/rightPanelStore"
import type { BrowserColorScheme } from "@/features/agents/browser/browserTabStore"
import type { BrowserViewport } from "@/features/agents/browser/browserViewport"
import { scopedThreadKey } from "@/features/agents/lib/rightPanelStore"

export interface BrowserTabController {
  navigate: (url: string) => Promise<void>
  goBack: () => Promise<void>
  goForward: () => Promise<void>
  reload: () => Promise<void>
  hardReload: () => Promise<void>
  zoomIn: () => Promise<void>
  zoomOut: () => Promise<void>
  resetZoom: () => Promise<void>
  setColorScheme: (scheme: BrowserColorScheme) => Promise<void>
  setViewport: (viewport: BrowserViewport) => Promise<void>
  /** Only the desktop webview can open Chromium DevTools. */
  openDevTools?: () => Promise<void>
}

const controllers = new Map<string, BrowserTabController>()
const listeners = new Map<string, Set<() => void>>()

export function browserControllerKey(
  ref: PanelThreadRef,
  tabId: string
): string {
  return `${scopedThreadKey(ref)}\u0000${tabId}`
}

function notify(key: string): void {
  for (const listener of listeners.get(key) ?? []) listener()
}

export function registerBrowserController(
  key: string,
  controller: BrowserTabController
): () => void {
  controllers.set(key, controller)
  notify(key)
  return () => {
    if (controllers.get(key) === controller) {
      controllers.delete(key)
      notify(key)
    }
  }
}

export function getBrowserController(key: string): BrowserTabController | null {
  return controllers.get(key) ?? null
}

export function subscribeBrowserController(
  key: string,
  listener: () => void
): () => void {
  const set = listeners.get(key) ?? new Set()
  set.add(listener)
  listeners.set(key, set)
  return () => {
    set.delete(listener)
    if (set.size === 0) listeners.delete(key)
  }
}
