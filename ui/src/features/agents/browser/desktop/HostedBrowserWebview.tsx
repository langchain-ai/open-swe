import { useCallback, useEffect, useRef, useState } from "react"
import { useShallow } from "zustand/react/shallow"

import type { DesktopBrowserBridge, DesktopBrowserConfig } from "@/desktop"
import type { PanelThreadRef } from "@/features/agents/lib/rightPanelStore"
import {
  ALLOW_POPUPS_ATTRIBUTE,
  type ElectronWebviewElement,
} from "@/features/agents/browser/desktop/webviewElement"
import { BrowserViewportFrame } from "@/features/agents/browser/BrowserViewportFrame"
import {
  type BrowserTabController,
  browserControllerKey,
  registerBrowserController,
} from "@/features/agents/browser/browserController"
import { useBrowserSurfaceStore } from "@/features/agents/browser/browserSurfaceStore"
import {
  type BrowserTabState,
  browserTabUrl,
  selectBrowserTab,
  useBrowserTabStore,
} from "@/features/agents/browser/browserTabStore"
import { normalizeBrowserUrl } from "@/features/agents/browser/browserUrl"
import { normalizeZoomFactor } from "@/features/agents/browser/browserViewport"
import { acquireDesktopTab } from "@/features/agents/browser/desktop/desktopTabLifetime"
import { resolveHostedBrowserWebviewWrapperStyle } from "@/features/agents/browser/desktop/hostedBrowserWebviewStyle"
import {
  INITIAL_WEBVIEW_CRASH_RECOVERY_STATE,
  type WebviewCrashRecoveryState,
  planWebviewCrashRecovery,
} from "@/features/agents/browser/desktop/webviewCrashRecovery"
import { cn } from "@/lib/utils"

const BLANK_URL = "about:blank"

function isMacPlatform(): boolean {
  return typeof navigator !== "undefined" && /Mac/.test(navigator.platform)
}

/**
 * One Electron `<webview>` for a desktop browser tab. It lives at the app root
 * and positions itself over the panel slot with `fixed` coordinates, so the
 * page survives tab switches, thread switches, and panel resizes without a
 * reload. Navigation state flows back from the main process into the tab
 * store; commands go the other way through the bridge.
 */
export function HostedBrowserWebview(props: {
  readonly threadRef: PanelThreadRef
  readonly tab: BrowserTabState
  readonly bridge: DesktopBrowserBridge
  readonly config: DesktopBrowserConfig
}) {
  const { threadRef, tab, bridge, config } = props
  const tabId = tab.tabId
  const [initialSrc] = useState(() => browserTabUrl(tab) ?? BLANK_URL)
  const wrapperRef = useRef<HTMLDivElement | null>(null)
  const webviewRef = useRef<ElectronWebviewElement | null>(null)
  const leaseRef = useRef<ReturnType<typeof acquireDesktopTab> | null>(null)
  const crashRecoveryRef = useRef<WebviewCrashRecoveryState>(
    INITIAL_WEBVIEW_CRASH_RECOVERY_STATE
  )
  const latestUrlRef = useRef(browserTabUrl(tab))
  const [generation, setGeneration] = useState(0)
  const [recoverySrc, setRecoverySrc] = useState(initialSrc)
  const presentation = useBrowserSurfaceStore(
    useShallow((state) => {
      const current = state.byTabId[tabId]
      return {
        rect: current?.rect ?? null,
        visible: current?.visible ?? false,
        zIndex: current?.zIndex ?? 30,
      }
    })
  )

  // Remembered for crash recovery, which remounts the guest at its last page.
  useEffect(() => {
    const url = browserTabUrl(tab)
    if (url) latestUrlRef.current = url
  }, [tab])

  // The main-process tab exists for as long as any webview renders it. Zoom
  // and appearance are read once at creation so the guest never paints a
  // frame at defaults; later changes go through the bridge.
  useEffect(() => {
    crashRecoveryRef.current = INITIAL_WEBVIEW_CRASH_RECOVERY_STATE
    const current = selectBrowserTab(
      useBrowserTabStore.getState().tabsByThreadKey,
      threadRef,
      tabId
    )
    const lease = acquireDesktopTab(bridge, tabId, {
      zoomFactor: current?.zoomFactor ?? 1,
      colorScheme: current?.colorScheme ?? "system",
    })
    leaseRef.current = lease
    return () => {
      if (leaseRef.current === lease) leaseRef.current = null
      lease.release()
    }
  }, [bridge, tabId, threadRef])

  useEffect(() => {
    const patch = (update: Partial<BrowserTabState>) =>
      useBrowserTabStore.getState().patchTab(threadRef, tabId, update)
    const controller: BrowserTabController = {
      navigate: (url) => bridge.navigate(tabId, normalizeBrowserUrl(url)),
      goBack: () => bridge.goBack(tabId),
      goForward: () => bridge.goForward(tabId),
      reload: () => bridge.reload(tabId),
      hardReload: () => bridge.hardReload(tabId),
      zoomIn: () => bridge.zoomIn(tabId),
      zoomOut: () => bridge.zoomOut(tabId),
      resetZoom: () => bridge.resetZoom(tabId),
      setColorScheme: (scheme) => bridge.setColorScheme(tabId, scheme),
      setViewport: async (viewport) => patch({ viewport }),
      openDevTools: () => bridge.openDevTools(tabId),
    }
    return registerBrowserController(
      browserControllerKey(threadRef, tabId),
      controller
    )
  }, [bridge, tabId, threadRef])

  const setWebviewRef = useCallback((node: HTMLElement | null) => {
    webviewRef.current = node as ElectronWebviewElement | null
  }, [])

  useEffect(() => {
    const webview = webviewRef.current
    if (!webview) return
    let disposed = false
    let recoveryTimeout: ReturnType<typeof setTimeout> | null = null
    const register = () => {
      const lease = leaseRef.current
      if (!lease) return
      void (async () => {
        try {
          // The main-process tab and the DOM webview are created by separate
          // effects; wait for the former so registration cannot race it.
          await lease.ready
          if (disposed || webviewRef.current !== webview) return
          const webContentsId = webview.getWebContentsId()
          if (Number.isInteger(webContentsId) && webContentsId > 0) {
            await bridge.registerWebview(tabId, webContentsId)
          }
        } catch {
          // did-attach / dom-ready fire again if the guest was not ready yet.
        }
      })()
    }
    const recoverGuest = () => {
      if (disposed || recoveryTimeout !== null) return
      const recovery = planWebviewCrashRecovery(
        crashRecoveryRef.current,
        Date.now()
      )
      if (!recovery) return
      crashRecoveryRef.current = recovery.state
      recoveryTimeout = setTimeout(() => {
        recoveryTimeout = null
        if (disposed) return
        setRecoverySrc(latestUrlRef.current ?? initialSrc)
        setGeneration((value) => value + 1)
      }, recovery.delayMs)
    }
    // A click inside the guest only reaches this document as a focus event,
    // so open menus never see the press that should dismiss them; replay it.
    const dismissHostPopups = () => {
      webview.dispatchEvent(
        new PointerEvent("pointerdown", { bubbles: true, pointerType: "mouse" })
      )
    }
    webview.addEventListener("did-attach", register)
    webview.addEventListener("dom-ready", register)
    webview.addEventListener("render-process-gone", recoverGuest)
    webview.addEventListener("focus", dismissHostPopups)
    register()
    return () => {
      disposed = true
      if (recoveryTimeout !== null) clearTimeout(recoveryTimeout)
      webview.removeEventListener("did-attach", register)
      webview.removeEventListener("dom-ready", register)
      webview.removeEventListener("render-process-gone", recoverGuest)
      webview.removeEventListener("focus", dismissHostPopups)
    }
  }, [bridge, generation, initialSrc, tabId])

  const active = presentation.visible && presentation.rect !== null
  const zoom = normalizeZoomFactor(tab.zoomFactor)
  const hiddenSize =
    tab.viewport.mode === "fill"
      ? {
          width: presentation.rect?.width ?? 1280,
          height: presentation.rect?.height ?? 800,
        }
      : { width: tab.viewport.width * zoom, height: tab.viewport.height * zoom }
  const containerSize =
    active && presentation.rect ? presentation.rect : hiddenSize
  const wrapperStyle = resolveHostedBrowserWebviewWrapperStyle({
    active,
    keepPaintableWhenInactive: isMacPlatform(),
    zIndex: presentation.zIndex,
    rect: presentation.rect,
    hiddenSize,
  })

  useEffect(() => {
    wrapperRef.current?.scrollTo({ left: 0, top: 0 })
  }, [tab.viewport, tabId])

  return (
    <div
      ref={wrapperRef}
      className="fixed overflow-hidden bg-muted/35"
      style={{ ...wrapperStyle, overscrollBehavior: "contain" }}
      data-browser-webview-host={tabId}
      data-browser-rendering={active ? "active" : "suspended"}
    >
      <BrowserViewportFrame
        containerSize={containerSize}
        viewport={tab.viewport}
        zoomFactor={zoom}
        onViewportChange={async (viewport) =>
          useBrowserTabStore.getState().patchTab(threadRef, tabId, { viewport })
        }
      >
        {(layout) => (
          <webview
            key={generation}
            ref={setWebviewRef}
            {...ALLOW_POPUPS_ATTRIBUTE}
            src={generation === 0 ? initialSrc : recoverySrc}
            partition={config.partition}
            webpreferences={config.webPreferences}
            data-browser-tab={tabId}
            aria-hidden={active ? undefined : true}
            className={cn(
              "absolute flex overflow-hidden bg-white",
              active && !layout.fillsPanel && "shadow-sm ring-1 ring-border/70"
            )}
            style={{
              left: layout.viewportX,
              top: layout.viewportY,
              width: layout.viewportWidth / layout.viewportScale,
              height: layout.viewportHeight / layout.viewportScale,
              transform:
                layout.viewportScale < 1
                  ? `scale(${layout.viewportScale})`
                  : undefined,
              transformOrigin: "top left",
            }}
          />
        )}
      </BrowserViewportFrame>
    </div>
  )
}
