/**
 * Connection to the Chromium running inside a cloud thread's sandbox.
 *
 * One session per thread, shared by every panel that shows one of its tabs.
 * It discovers page targets (including ones the agent opened), mirrors them
 * into the tab store and the right-panel tab strip, and hands each visible
 * tab a page session for input and screencast.
 */
import { useEffect, useState } from "react"

import type { PanelThreadRef } from "@/features/agents/lib/rightPanelStore"
import {
  type BrowserNavStatus,
  type BrowserTabState,
  type CloudBrowserConnectionState,
  useBrowserTabStore,
} from "@/features/agents/browser/browserTabStore"
import {
  type BrowserTabController,
  browserControllerKey,
  registerBrowserController,
} from "@/features/agents/browser/browserController"
import { normalizeBrowserUrl } from "@/features/agents/browser/browserUrl"
import { stepBrowserZoom } from "@/features/agents/browser/browserViewport"
import {
  CdpConnection,
  type CdpControlMessage,
} from "@/features/agents/browser/cdp/cdpConnection"
import {
  type CdpPageNavigationUpdate,
  CdpPageSession,
  type CdpScreencastFrame,
} from "@/features/agents/browser/cdp/cdpPageSession"
import { agentsApi } from "@/features/agents/lib/api"
import {
  scopedThreadKey,
  useRightPanelStore,
} from "@/features/agents/lib/rightPanelStore"

interface TargetInfo {
  readonly targetId: string
  readonly type: string
  readonly title: string
  readonly url: string
  readonly attached: boolean
}

type FrameListener = (frame: CdpScreencastFrame) => void

const BLANK_URL = "about:blank"
const HIDDEN_URL_PREFIXES = ["devtools://", "chrome-extension://", "chrome://"]

export function isVisiblePageTarget(info: TargetInfo): boolean {
  return (
    info.type === "page" &&
    !HIDDEN_URL_PREFIXES.some((prefix) => info.url.startsWith(prefix))
  )
}

/** Folds a target's url/title into the tab's navigation state. */
export function navFromTarget(
  current: BrowserNavStatus,
  info: { url: string; title: string }
): BrowserNavStatus {
  if (info.url === BLANK_URL || info.url === "") return { kind: "idle" }
  const title = info.title === BLANK_URL ? "" : info.title
  if (current.kind === "loading")
    return { kind: "loading", url: info.url, title }
  if (current.kind === "failed" && current.url === info.url) {
    return { ...current, title }
  }
  return { kind: "success", url: info.url, title }
}

/** Applies a page-session update on top of the current navigation state. */
export function applyNavigationUpdate(
  current: BrowserNavStatus,
  update: CdpPageNavigationUpdate
): BrowserNavStatus {
  const url = update.url ?? (current.kind === "idle" ? null : current.url)
  if (url === null || url === BLANK_URL) return { kind: "idle" }
  const title =
    current.kind === "idle" || current.url !== url ? "" : current.title
  if (update.failed) {
    return {
      kind: "failed",
      url,
      title,
      code: null,
      description: update.failed,
    }
  }
  if (update.loading === true) return { kind: "loading", url, title }
  if (update.loading === false) {
    if (current.kind !== "failed" || update.failed === null) {
      return { kind: "success", url, title }
    }
    // Chromium reports "stopped loading" for its own error page too; the
    // failure stands until a navigation actually commits.
    return url === current.url ? current : { ...current, url }
  }
  if (current.kind === "loading") return { kind: "loading", url, title }
  if (current.kind === "failed" && update.failed === undefined) return current
  return { kind: "success", url, title }
}

export class CloudBrowserSession {
  private connection: CdpConnection
  private readonly pages = new Map<string, CdpPageSession>()
  private readonly pagesBySessionId = new Map<string, CdpPageSession>()
  private readonly pending = new Map<string, Promise<CdpPageSession>>()
  private readonly frameListeners = new Map<string, Set<FrameListener>>()
  private readonly controllerCleanups = new Map<string, () => void>()
  private readonly targets = new Map<string, TargetInfo>()
  private readonly userCreated = new Set<string>()
  private ready = false
  private disposed = false

  constructor(readonly ref: PanelThreadRef) {
    this.connection = this.createConnection()
  }

  private createConnection(): CdpConnection {
    return new CdpConnection(
      () => agentsApi.connectCloudBrowser(this.ref.threadId),
      {
        onConnecting: () =>
          this.setConnection({ status: "connecting", error: null }),
        onReady: () => {
          this.ready = true
          this.setConnection({ status: "ready", error: null })
          void this.discover()
        },
        onControl: (message) => this.handleControl(message),
        onEvent: (method, params, sessionId) =>
          this.handleEvent(method, params, sessionId),
        onClose: (willRetry, reason) => {
          this.ready = false
          this.dropPages()
          this.setConnection({
            status: willRetry ? "connecting" : "closed",
            error: reason,
          })
        },
      }
    )
  }

  start(): void {
    this.connection.start()
  }

  dispose(): void {
    this.disposed = true
    this.dropPages()
    for (const cleanup of this.controllerCleanups.values()) cleanup()
    this.controllerCleanups.clear()
    this.connection.dispose()
  }

  requestInstall(): void {
    useBrowserTabStore.getState().setConnection(this.ref, {
      status: "installing",
      installOutput: [],
      error: null,
    })
    this.connection.requestInstall()
  }

  /** Reconnects after a final close or error. */
  retry(): void {
    if (this.disposed) return
    this.connection.dispose()
    this.ready = false
    this.dropPages()
    // A disposed connection never reopens, so a retry gets a fresh one.
    this.connection = this.createConnection()
    this.connection.start()
  }

  private setConnection(
    patch: Partial<Pick<CloudBrowserConnectionState, "status" | "error">>
  ): void {
    useBrowserTabStore.getState().setConnection(this.ref, patch)
  }

  private handleControl(message: CdpControlMessage): void {
    const store = useBrowserTabStore.getState()
    switch (message.type) {
      case "ready":
        return
      case "browser-missing":
        store.setConnection(this.ref, {
          status: "browser-missing",
          installCommand: message.installCommand ?? null,
          error: null,
        })
        return
      case "installing":
        store.setConnection(this.ref, { status: "installing", error: null })
        return
      case "install-output":
        store.appendInstallOutput(this.ref, message.data)
        return
      case "error":
        store.setConnection(this.ref, {
          status: "error",
          error: message.message,
        })
        return
    }
  }

  private handleEvent(
    method: string,
    params: unknown,
    sessionId: string | undefined
  ): void {
    if (sessionId) {
      this.pagesBySessionId.get(sessionId)?.handleEvent(method, params)
      return
    }
    switch (method) {
      case "Target.targetCreated":
      case "Target.targetInfoChanged": {
        const { targetInfo } = params as { targetInfo: TargetInfo }
        this.upsertTarget(targetInfo, method === "Target.targetCreated")
        return
      }
      case "Target.targetDestroyed": {
        const { targetId } = params as { targetId: string }
        this.removeTarget(targetId)
        return
      }
      case "Target.detachedFromTarget": {
        const { sessionId: detached } = params as { sessionId: string }
        const page = this.pagesBySessionId.get(detached)
        if (page) {
          this.pagesBySessionId.delete(detached)
          this.pages.delete(page.targetId)
          page.markDisconnected()
          useBrowserTabStore.getState().patchTab(this.ref, page.targetId, {
            attached: false,
          })
        }
        return
      }
      default:
        return
    }
  }

  private async discover(): Promise<void> {
    try {
      await this.connection.send("Target.setDiscoverTargets", {
        discover: true,
      })
      const { targetInfos } = await this.connection.send<{
        targetInfos: Array<TargetInfo>
      }>("Target.getTargets")
      const live = new Set<string>()
      for (const info of targetInfos) {
        if (!isVisiblePageTarget(info)) continue
        live.add(info.targetId)
        this.upsertTarget(info, false)
      }
      // Collected first: removeTarget mutates the map being walked.
      const stale = Array.from(this.targets.keys()).filter(
        (id) => !live.has(id)
      )
      for (const targetId of stale) this.removeTarget(targetId)
      this.reconcileSurfaces()
    } catch (error: unknown) {
      if (this.disposed) return
      this.setConnection({
        status: "error",
        error:
          error instanceof Error
            ? error.message
            : "Could not list browser tabs.",
      })
    }
  }

  private upsertTarget(info: TargetInfo, created: boolean): void {
    if (!isVisiblePageTarget(info)) {
      if (this.targets.has(info.targetId)) this.removeTarget(info.targetId)
      return
    }
    const known = this.targets.has(info.targetId)
    this.targets.set(info.targetId, info)
    const store = useBrowserTabStore.getState()
    const current =
      store.tabsByThreadKey[scopedThreadKey(this.ref)]?.[info.targetId]
    store.upsertTab(this.ref, {
      tabId: info.targetId,
      host: "cloud",
      nav: navFromTarget(current?.nav ?? { kind: "idle" }, info),
    })
    this.ensureController(info.targetId)
    if (!known && created) {
      // Tabs the user asked for become active; tabs the agent opened only
      // join the strip so they do not steal what the user is looking at.
      const activate = this.userCreated.delete(info.targetId)
      useRightPanelStore
        .getState()
        .openBrowser(this.ref, info.targetId, { activate })
    }
  }

  private removeTarget(targetId: string): void {
    if (!this.targets.delete(targetId)) return
    const page = this.pages.get(targetId)
    if (page) {
      this.pages.delete(targetId)
      if (page.attachedSessionId)
        this.pagesBySessionId.delete(page.attachedSessionId)
      page.markDisconnected()
    }
    this.controllerCleanups.get(targetId)?.()
    this.controllerCleanups.delete(targetId)
    this.frameListeners.delete(targetId)
    useBrowserTabStore.getState().removeTab(this.ref, targetId)
    this.reconcileSurfaces()
  }

  private reconcileSurfaces(): void {
    useRightPanelStore
      .getState()
      .reconcileBrowserSurfaces(this.ref, [...this.targets.keys()])
  }

  /** Opens a new page target; resolves with its id once Chromium reports it. */
  async createTab(url?: string): Promise<string> {
    const { targetId } = await this.connection.send<{ targetId: string }>(
      "Target.createTarget",
      { url: url ?? BLANK_URL }
    )
    this.userCreated.add(targetId)
    if (this.targets.has(targetId)) {
      // The created event beat the response; activate it now.
      this.userCreated.delete(targetId)
      useRightPanelStore
        .getState()
        .openBrowser(this.ref, targetId, { activate: true })
    }
    return targetId
  }

  async closeTab(targetId: string): Promise<void> {
    if (!this.ready) {
      this.removeTarget(targetId)
      return
    }
    await this.connection.send("Target.closeTarget", { targetId })
  }

  hasTarget(targetId: string): boolean {
    return this.targets.has(targetId)
  }

  /** Attaches to a target on first use; subsequent calls share the session. */
  ensurePage(targetId: string): Promise<CdpPageSession> {
    const existing = this.pages.get(targetId)
    if (existing?.attachedSessionId) return Promise.resolve(existing)
    const pending = this.pending.get(targetId)
    if (pending) return pending
    const page = new CdpPageSession(this.connection, targetId, {
      onNavigation: (update) => {
        const store = useBrowserTabStore.getState()
        const current =
          store.tabsByThreadKey[scopedThreadKey(this.ref)]?.[targetId]
        if (!current) return
        store.patchTab(this.ref, targetId, {
          nav: applyNavigationUpdate(current.nav, update),
        })
      },
      onHistory: (history) =>
        useBrowserTabStore.getState().patchTab(this.ref, targetId, history),
      onFrame: (frame) => {
        for (const listener of this.frameListeners.get(targetId) ?? [])
          listener(frame)
      },
    })
    const attach = page
      .attach()
      .then(() => {
        this.pending.delete(targetId)
        if (this.disposed || !page.attachedSessionId) {
          throw new Error("The browser tab is no longer available.")
        }
        this.pages.set(targetId, page)
        this.pagesBySessionId.set(page.attachedSessionId, page)
        useBrowserTabStore
          .getState()
          .patchTab(this.ref, targetId, { attached: true })
        return page
      })
      .catch((error: unknown) => {
        this.pending.delete(targetId)
        throw error
      })
    this.pending.set(targetId, attach)
    return attach
  }

  onFrame(targetId: string, listener: FrameListener): () => void {
    const listeners = this.frameListeners.get(targetId) ?? new Set()
    listeners.add(listener)
    this.frameListeners.set(targetId, listeners)
    return () => {
      listeners.delete(listener)
      if (listeners.size === 0) this.frameListeners.delete(targetId)
    }
  }

  private dropPages(): void {
    for (const page of this.pages.values()) page.markDisconnected()
    this.pages.clear()
    this.pagesBySessionId.clear()
    this.pending.clear()
    const store = useBrowserTabStore.getState()
    for (const targetId of this.targets.keys()) {
      store.patchTab(this.ref, targetId, { attached: false })
    }
  }

  private ensureController(targetId: string): void {
    if (this.controllerCleanups.has(targetId)) return
    const withPage = async (use: (page: CdpPageSession) => Promise<void>) => {
      const page = await this.ensurePage(targetId)
      await use(page)
    }
    const patch = (update: Partial<BrowserTabState>) =>
      useBrowserTabStore.getState().patchTab(this.ref, targetId, update)
    const currentZoom = () =>
      useBrowserTabStore.getState().tabsByThreadKey[
        scopedThreadKey(this.ref)
      ]?.[targetId]?.zoomFactor ?? 1
    const controller: BrowserTabController = {
      navigate: (url) =>
        withPage((page) => page.navigate(normalizeBrowserUrl(url))),
      goBack: () => withPage((page) => page.goBack()),
      goForward: () => withPage((page) => page.goForward()),
      reload: () => withPage((page) => page.reload(false)),
      hardReload: () => withPage((page) => page.reload(true)),
      zoomIn: async () =>
        patch({ zoomFactor: stepBrowserZoom(currentZoom(), 1) }),
      zoomOut: async () =>
        patch({ zoomFactor: stepBrowserZoom(currentZoom(), -1) }),
      resetZoom: async () => patch({ zoomFactor: 1 }),
      setColorScheme: async (scheme) => {
        patch({ colorScheme: scheme })
        await withPage((page) => page.setColorScheme(scheme))
      },
      setViewport: async (viewport) => patch({ viewport }),
    }
    this.controllerCleanups.set(
      targetId,
      registerBrowserController(
        browserControllerKey(this.ref, targetId),
        controller
      )
    )
  }
}

interface SessionEntry {
  readonly session: CloudBrowserSession
  refs: number
  disposeTimer: ReturnType<typeof setTimeout> | null
}

const sessions = new Map<string, SessionEntry>()
const DISPOSE_DELAY_MS = 30_000

/**
 * Shares one connection per thread. The connection lingers briefly after the
 * last release so switching tabs or panels does not reconnect to the sandbox.
 */
export function acquireCloudBrowser(ref: PanelThreadRef): {
  readonly session: CloudBrowserSession
  readonly release: () => void
} {
  const key = scopedThreadKey(ref)
  let entry = sessions.get(key)
  if (!entry) {
    const session = new CloudBrowserSession(ref)
    entry = { session, refs: 0, disposeTimer: null }
    sessions.set(key, entry)
    session.start()
  }
  if (entry.disposeTimer) {
    clearTimeout(entry.disposeTimer)
    entry.disposeTimer = null
  }
  entry.refs += 1
  const current = entry
  let released = false
  return {
    session: current.session,
    release: () => {
      if (released) return
      released = true
      current.refs = Math.max(0, current.refs - 1)
      if (current.refs > 0) return
      current.disposeTimer = setTimeout(() => {
        if (current.refs > 0 || sessions.get(key) !== current) return
        sessions.delete(key)
        current.session.dispose()
        useBrowserTabStore.getState().setConnection(ref, { status: "idle" })
      }, DISPOSE_DELAY_MS)
    },
  }
}

export function getCloudBrowserSession(
  ref: PanelThreadRef
): CloudBrowserSession | null {
  return sessions.get(scopedThreadKey(ref))?.session ?? null
}

/** Keeps the thread's sandbox browser connection alive while `enabled`. */
export function useCloudBrowserSession(
  ref: PanelThreadRef,
  enabled: boolean
): CloudBrowserSession | null {
  const [session, setSession] = useState<CloudBrowserSession | null>(null)
  useEffect(() => {
    if (!enabled) {
      // oxlint-disable-next-line react/set-state-in-effect
      setSession(null)
      return
    }
    const acquired = acquireCloudBrowser({
      scope: ref.scope,
      threadId: ref.threadId,
    })
    setSession(acquired.session)
    return () => {
      acquired.release()
    }
  }, [enabled, ref.scope, ref.threadId])
  return session
}
