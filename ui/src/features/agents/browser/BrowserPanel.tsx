import { useCallback, useEffect, useRef } from "react"
import { toast } from "sonner"

import type { PanelThreadRef } from "@/features/agents/lib/rightPanelStore"
import type { CloudBrowserSession } from "@/features/agents/browser/cloudBrowserSession"
import { BrowserChromeRow } from "@/features/agents/browser/BrowserChromeRow"
import { BrowserEmptyState } from "@/features/agents/browser/BrowserEmptyState"
import { BrowserMoreMenu } from "@/features/agents/browser/BrowserMoreMenu"
import { BrowserSurfaceSlot } from "@/features/agents/browser/BrowserSurfaceSlot"
import { BrowserUnreachable } from "@/features/agents/browser/BrowserUnreachable"
import { CloudBrowserStatus } from "@/features/agents/browser/CloudBrowserStatus"
import { CloudBrowserSurface } from "@/features/agents/browser/CloudBrowserSurface"
import { ZoomIndicator } from "@/features/agents/browser/ZoomIndicator"
import {
  BROWSER_HISTORY_MAX_ENTRIES_PER_SCOPE,
  useBrowserHistoryStore,
  useRecentBrowserHistory,
} from "@/features/agents/browser/browserHistoryStore"
import {
  type BrowserHostKind,
  selectBrowserTab,
  selectCloudBrowserConnection,
  useBrowserTabStore,
} from "@/features/agents/browser/browserTabStore"
import {
  BrowserUrlError,
  normalizeBrowserUrl,
} from "@/features/agents/browser/browserUrl"
import {
  FILL_VIEWPORT,
  resolveResponsiveBrowserViewportSize,
} from "@/features/agents/browser/browserViewport"
import { openBrowserTabWithUrl } from "@/features/agents/browser/openBrowserTab"
import { useBrowserController } from "@/features/agents/browser/useBrowserController"

export interface BrowserPanelProps {
  threadRef: PanelThreadRef
  host: BrowserHostKind
  /** `null` is the "new tab" placeholder: an address bar over recent pages. */
  tabId: string | null
  visible: boolean
  /** Groups recently visited URLs, e.g. by repository. */
  historyScope: string
  cloudSession: CloudBrowserSession | null
}

const HINTS: Record<BrowserHostKind, string> = {
  desktop:
    "Type a URL above. Dev servers on this machine, like localhost:3000, open right here.",
  cloud:
    "Type a URL above. Servers the agent starts in its sandbox, like localhost:3000, open right here.",
}

function reportError(title: string, error: unknown): void {
  toast.error(title, {
    description:
      error instanceof Error ? error.message : "Something went wrong.",
  })
}

/**
 * One browser tab: chrome row on top, the page surface below. The surface is
 * an Electron webview for local sessions and a screencast canvas for a cloud
 * sandbox, chosen by `host`; everything else is shared.
 */
export function BrowserPanel(props: BrowserPanelProps) {
  const { threadRef, host, tabId, visible, historyScope, cloudSession } = props
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const tab = useBrowserTabStore((state) =>
    tabId ? selectBrowserTab(state.tabsByThreadKey, threadRef, tabId) : null
  )
  const connection = useBrowserTabStore((state) =>
    selectCloudBrowserConnection(state.connectionByThreadKey, threadRef)
  )
  const controller = useBrowserController(threadRef, tabId)
  const recents = useRecentBrowserHistory(
    historyScope,
    BROWSER_HISTORY_MAX_ENTRIES_PER_SCOPE
  )
  const recordVisit = useBrowserHistoryStore((state) => state.recordVisit)
  const setTitle = useBrowserHistoryStore((state) => state.setTitle)
  const removeUrl = useBrowserHistoryStore((state) => state.removeUrl)

  const nav = tab?.nav ?? { kind: "idle" as const }
  const url = nav.kind === "idle" ? "" : nav.url
  const loading = nav.kind === "loading"
  const failed = nav.kind === "failed" ? nav : null
  const showEmptyState = !tab || nav.kind === "idle"

  // A page title enriches the history entry it was opened from.
  const navTitle = nav.kind === "success" ? nav.title : null
  useEffect(() => {
    if (!url || !navTitle) return
    setTitle(historyScope, url, navTitle)
  }, [historyScope, navTitle, setTitle, url])

  const navigateTo = useCallback(
    async (raw: string) => {
      let normalized: string
      try {
        normalized = normalizeBrowserUrl(raw)
      } catch (error: unknown) {
        if (error instanceof BrowserUrlError) toast.error(error.message)
        return
      }
      try {
        if (tab && controller) {
          await controller.navigate(normalized)
        } else {
          await openBrowserTabWithUrl({
            threadRef,
            host,
            url: normalized,
            session: cloudSession,
          })
        }
        recordVisit(historyScope, normalized)
      } catch (error: unknown) {
        reportError("Unable to open page", error)
      }
    },
    [cloudSession, controller, historyScope, host, recordVisit, tab, threadRef]
  )

  const run = useCallback(
    (operation: (() => Promise<void>) | undefined, title: string) => {
      if (!operation) return
      void operation().catch((error: unknown) => reportError(title, error))
    },
    []
  )

  const handleToggleDeviceToolbar = useCallback(() => {
    if (!controller || !tab) return
    if (tab.viewport.mode !== "fill") {
      run(
        () => controller.setViewport(FILL_VIEWPORT),
        "Unable to resize viewport"
      )
      return
    }
    const rect = bodyRef.current?.getBoundingClientRect()
    const size = rect
      ? resolveResponsiveBrowserViewportSize(
          { width: rect.width, height: rect.height },
          tab.zoomFactor
        )
      : { width: 1024, height: 768 }
    run(
      () => controller.setViewport({ mode: "freeform", ...size }),
      "Unable to resize viewport"
    )
  }, [controller, run, tab])

  const handleOpenExternal = useCallback(() => {
    if (!url) return
    const desktop =
      typeof window === "undefined" ? undefined : window.openSweDesktop
    if (desktop) {
      void desktop
        .openExternal(url)
        .catch((error: unknown) =>
          reportError("Unable to open the system browser", error)
        )
      return
    }
    window.open(url, "_blank", "noopener,noreferrer")
  }, [url])

  const cloudStatusVisible =
    host === "cloud" &&
    connection.status !== "ready" &&
    connection.status !== "idle"
  const surfaceVisible = visible && !failed && !cloudStatusVisible

  return (
    <div
      className="flex min-h-0 flex-1 flex-col bg-background"
      data-browser-panel
    >
      <BrowserChromeRow
        url={url}
        loading={loading}
        canGoBack={tab?.canGoBack ?? false}
        canGoForward={tab?.canGoForward ?? false}
        refreshDisabled={!tab || nav.kind === "idle" || !controller}
        autoFocus={tabId === null && visible}
        onBack={() => run(controller?.goBack, "Unable to go back")}
        onForward={() => run(controller?.goForward, "Unable to go forward")}
        onRefresh={() => run(controller?.reload, "Unable to reload")}
        onSubmit={(next) => void navigateTo(next)}
        onOpenExternal={url ? handleOpenExternal : undefined}
        trailingActions={
          <BrowserMoreMenu
            tab={tab}
            controller={controller}
            onToggleDeviceToolbar={handleToggleDeviceToolbar}
          />
        }
      />
      <div ref={bodyRef} className="relative min-h-0 flex-1 overflow-hidden">
        {tab && !showEmptyState && host === "desktop" ? (
          <BrowserSurfaceSlot
            key={tab.tabId}
            tabId={tab.tabId}
            visible={surfaceVisible}
            className="absolute inset-0 h-full w-full"
          />
        ) : null}
        {tab && !showEmptyState && host === "cloud" && cloudSession ? (
          <CloudBrowserSurface
            key={tab.tabId}
            session={cloudSession}
            tab={tab}
            visible={surfaceVisible}
            onViewportChange={(viewport) =>
              controller?.setViewport(viewport) ?? Promise.resolve()
            }
          />
        ) : null}
        {showEmptyState ? (
          <BrowserEmptyState
            entries={recents}
            hint={HINTS[host]}
            onOpenUrl={(next) => void navigateTo(next)}
            onRemove={(next) => removeUrl(historyScope, next)}
          />
        ) : null}
        {tab ? <ZoomIndicator zoomFactor={tab.zoomFactor} /> : null}
        {failed ? (
          <div className="absolute inset-0 z-10 bg-background">
            <BrowserUnreachable
              url={failed.url}
              code={failed.code}
              description={failed.description}
              onReload={() => run(controller?.reload, "Unable to reload")}
            />
          </div>
        ) : null}
        {cloudStatusVisible ? (
          <CloudBrowserStatus
            connection={connection}
            onInstall={() => cloudSession?.requestInstall()}
            onRetry={() => cloudSession?.retry()}
          />
        ) : null}
      </div>
    </div>
  )
}
