import { useEffect, useMemo, useState } from "react"
import { useShallow } from "zustand/react/shallow"

import type { DesktopBrowserConfig, DesktopBrowserTabState } from "@/desktop"
import type { BrowserNavStatus } from "@/features/agents/browser/browserTabStore"
import { HostedBrowserWebview } from "@/features/agents/browser/desktop/HostedBrowserWebview"
import { readDesktopBrowserBridge } from "@/features/agents/browser/desktop/desktopBrowserBridge"
import { useBrowserTabStore } from "@/features/agents/browser/browserTabStore"
import {
  parseScopedThreadKey,
  useRightPanelStore,
} from "@/features/agents/lib/rightPanelStore"

/** Translates the main-process nav status into the tab store's shape. */
export function navFromDesktopState(
  nav: DesktopBrowserTabState["nav"]
): BrowserNavStatus {
  switch (nav.kind) {
    case "idle":
      return { kind: "idle" }
    case "loading":
    case "success":
      return { kind: nav.kind, url: nav.url, title: nav.title }
    case "failed":
      return {
        kind: "failed",
        url: nav.url,
        title: nav.title,
        code: nav.code,
        description: nav.description,
      }
  }
}

/**
 * Mounted once at the app root in the desktop app. Renders a webview for
 * every desktop browser tab that some thread's right panel currently lists,
 * and mirrors main-process state changes into the tab store.
 */
export function DesktopBrowserHost() {
  const bridge = useMemo(() => readDesktopBrowserBridge(), [])
  const [config, setConfig] = useState<DesktopBrowserConfig | null>(null)

  useEffect(() => {
    if (!bridge) return
    let disposed = false
    bridge
      .getConfig()
      .then((next) => {
        if (!disposed) setConfig(next)
      })
      .catch((error: unknown) => {
        console.warn("Could not load the desktop browser configuration", error)
      })
    return () => {
      disposed = true
    }
  }, [bridge])

  useEffect(() => {
    if (!bridge) return
    return bridge.onStateChange((state) => {
      const store = useBrowserTabStore.getState()
      for (const [threadKey, tabs] of Object.entries(store.tabsByThreadKey)) {
        const tab = tabs[state.tabId]
        if (!tab || tab.host !== "desktop") continue
        const ref = parseScopedThreadKey(threadKey)
        if (!ref) continue
        store.patchTab(ref, state.tabId, {
          nav: navFromDesktopState(state.nav),
          canGoBack: state.canGoBack,
          canGoForward: state.canGoForward,
          zoomFactor: state.zoomFactor,
          colorScheme: state.colorScheme,
          favicon: state.favicon,
          attached: state.webContentsId !== null,
        })
        return
      }
    })
  }, [bridge])

  // Only tabs a panel lists get a webview; stale persisted tabs stay dormant.
  const listedTabKeys = useRightPanelStore(
    useShallow((state) =>
      Object.entries(state.byThreadKey).flatMap(([threadKey, thread]) =>
        thread.surfaces.flatMap((surface) =>
          surface.kind === "preview" && surface.resourceId
            ? [`${threadKey}\u0000${surface.resourceId}`]
            : []
        )
      )
    )
  )
  const tabsByThreadKey = useBrowserTabStore((state) => state.tabsByThreadKey)
  const entries = useMemo(
    () =>
      listedTabKeys.flatMap((key) => {
        const [threadKey, tabId] = key.split("\u0000")
        if (!threadKey || !tabId) return []
        const tab = tabsByThreadKey[threadKey]?.[tabId]
        const ref = parseScopedThreadKey(threadKey)
        return tab?.host === "desktop" && ref ? [{ ref, tab }] : []
      }),
    [listedTabKeys, tabsByThreadKey]
  )

  if (!bridge || !config) return null
  return (
    <div className="contents" data-desktop-browser-host>
      {entries.map(({ ref, tab }) => (
        <HostedBrowserWebview
          key={tab.tabId}
          threadRef={ref}
          tab={tab}
          bridge={bridge}
          config={config}
        />
      ))}
    </div>
  )
}
