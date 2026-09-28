/**
 * One attached page target in the sandbox browser: navigation, history,
 * emulation, input, and the screencast that paints it into the panel.
 */
import {
  CdpConnection,
  CdpDisconnectedError,
} from "@/features/agents/browser/cdp/cdpConnection"

export interface CdpScreencastFrameMetadata {
  readonly deviceWidth: number
  readonly deviceHeight: number
  readonly pageScaleFactor: number
  readonly scrollOffsetX: number
  readonly scrollOffsetY: number
  readonly offsetTop: number
}

export interface CdpScreencastFrame {
  /** Base64 JPEG. */
  readonly data: string
  readonly metadata: CdpScreencastFrameMetadata
  readonly sessionId: number
}

export interface CdpMouseEventInput {
  readonly type: "mousePressed" | "mouseReleased" | "mouseMoved" | "mouseWheel"
  readonly x: number
  readonly y: number
  readonly button?: "none" | "left" | "middle" | "right" | "back" | "forward"
  readonly buttons?: number
  readonly clickCount?: number
  readonly modifiers?: number
  readonly deltaX?: number
  readonly deltaY?: number
}

export interface CdpDeviceMetrics {
  readonly width: number
  readonly height: number
  readonly deviceScaleFactor: number
}

export interface CdpScreencastOptions {
  readonly maxWidth: number
  readonly maxHeight: number
}

export interface CdpPageNavigationUpdate {
  readonly url?: string
  readonly loading?: boolean
  /** Chromium error text for a failed main-frame load, or null when it succeeded. */
  readonly failed?: string | null
}

export interface CdpPageSessionCallbacks {
  readonly onNavigation: (update: CdpPageNavigationUpdate) => void
  readonly onHistory: (state: {
    canGoBack: boolean
    canGoForward: boolean
  }) => void
  readonly onFrame: (frame: CdpScreencastFrame) => void
}

interface FrameInfo {
  readonly id: string
  readonly parentId?: string
  readonly url: string
  readonly unreachableUrl?: string
}

interface NavigationHistory {
  readonly currentIndex: number
  readonly entries: ReadonlyArray<{ readonly id: number; readonly url: string }>
}

const ERROR_PAGE_PREFIX = "chrome-error://"

export class CdpPageSession {
  private sessionId: string | null = null
  private mainFrameId: string | null = null
  private lastNavigateError: string | null = null
  private screencast: CdpScreencastOptions | null = null
  private disposed = false

  constructor(
    private readonly connection: CdpConnection,
    readonly targetId: string,
    private readonly callbacks: CdpPageSessionCallbacks
  ) {}

  get attachedSessionId(): string | null {
    return this.sessionId
  }

  async attach(): Promise<void> {
    const { sessionId } = await this.connection.send<{ sessionId: string }>(
      "Target.attachToTarget",
      { targetId: this.targetId, flatten: true }
    )
    if (this.disposed) {
      await this.connection
        .send("Target.detachFromTarget", { sessionId })
        .catch(() => undefined)
      return
    }
    this.sessionId = sessionId
    await this.send("Page.enable")
    const tree = await this.send<{ frameTree: { frame: FrameInfo } }>(
      "Page.getFrameTree"
    )
    this.mainFrameId = tree.frameTree.frame.id
    // Headless pages otherwise report document.hasFocus() === false, which
    // makes many apps skip focus styling and autofocus.
    await this.send("Emulation.setFocusEmulationEnabled", {
      enabled: true,
    }).catch(() => undefined)
    await this.refreshHistory()
  }

  /** Routes a CDP event that arrived with this session's id. */
  handleEvent(method: string, params: unknown): void {
    switch (method) {
      case "Page.screencastFrame": {
        const frame = params as CdpScreencastFrame
        this.callbacks.onFrame(frame)
        void this.send("Page.screencastFrameAck", {
          sessionId: frame.sessionId,
        }).catch(() => undefined)
        return
      }
      case "Page.frameNavigated": {
        const { frame } = params as { frame: FrameInfo }
        if (frame.parentId) return
        this.mainFrameId = frame.id
        if (frame.unreachableUrl || frame.url.startsWith(ERROR_PAGE_PREFIX)) {
          this.callbacks.onNavigation({
            url: frame.unreachableUrl ?? frame.url,
            loading: false,
            failed: this.lastNavigateError ?? "ERR_FAILED",
          })
        } else {
          this.callbacks.onNavigation({ url: frame.url, failed: null })
        }
        void this.refreshHistory()
        return
      }
      case "Page.navigatedWithinDocument": {
        const { frameId, url } = params as { frameId: string; url: string }
        if (frameId !== this.mainFrameId) return
        this.callbacks.onNavigation({ url })
        void this.refreshHistory()
        return
      }
      case "Page.frameStartedLoading": {
        const { frameId } = params as { frameId: string }
        if (frameId !== this.mainFrameId) return
        this.callbacks.onNavigation({ loading: true })
        return
      }
      case "Page.frameStoppedLoading": {
        const { frameId } = params as { frameId: string }
        if (frameId !== this.mainFrameId) return
        this.callbacks.onNavigation({ loading: false })
        void this.refreshHistory()
        return
      }
      default:
        return
    }
  }

  async navigate(url: string): Promise<void> {
    this.lastNavigateError = null
    this.callbacks.onNavigation({ url, loading: true, failed: null })
    const result = await this.send<{ errorText?: string }>("Page.navigate", {
      url,
    })
    if (result.errorText) {
      this.lastNavigateError = result.errorText
      this.callbacks.onNavigation({
        url,
        loading: false,
        failed: result.errorText,
      })
    }
    await this.refreshHistory()
  }

  reload(ignoreCache = false): Promise<void> {
    this.lastNavigateError = null
    return this.send("Page.reload", { ignoreCache })
  }

  async goBack(): Promise<void> {
    await this.navigateHistory(-1)
  }

  async goForward(): Promise<void> {
    await this.navigateHistory(1)
  }

  private async navigateHistory(offset: 1 | -1): Promise<void> {
    const history = await this.send<NavigationHistory>(
      "Page.getNavigationHistory"
    )
    const entry = history.entries[history.currentIndex + offset]
    if (!entry) return
    this.lastNavigateError = null
    await this.send("Page.navigateToHistoryEntry", { entryId: entry.id })
  }

  async refreshHistory(): Promise<void> {
    if (!this.sessionId) return
    try {
      const history = await this.send<NavigationHistory>(
        "Page.getNavigationHistory"
      )
      this.callbacks.onHistory({
        canGoBack: history.currentIndex > 0,
        canGoForward: history.currentIndex < history.entries.length - 1,
      })
    } catch (error: unknown) {
      if (!(error instanceof CdpDisconnectedError)) throw error
    }
  }

  setDeviceMetrics(metrics: CdpDeviceMetrics): Promise<void> {
    return this.send("Emulation.setDeviceMetricsOverride", {
      width: Math.max(1, Math.round(metrics.width)),
      height: Math.max(1, Math.round(metrics.height)),
      deviceScaleFactor: metrics.deviceScaleFactor,
      mobile: false,
    })
  }

  async startScreencast(options: CdpScreencastOptions): Promise<void> {
    this.screencast = options
    await this.send("Page.startScreencast", {
      format: "jpeg",
      quality: 80,
      maxWidth: Math.max(1, Math.round(options.maxWidth)),
      maxHeight: Math.max(1, Math.round(options.maxHeight)),
      everyNthFrame: 1,
    })
  }

  async stopScreencast(): Promise<void> {
    if (!this.screencast) return
    this.screencast = null
    await this.send("Page.stopScreencast").catch(() => undefined)
  }

  setColorScheme(scheme: "system" | "light" | "dark"): Promise<void> {
    return this.send("Emulation.setEmulatedMedia", {
      features: [
        // An empty value clears the override so the page follows the OS.
        {
          name: "prefers-color-scheme",
          value: scheme === "system" ? "" : scheme,
        },
      ],
    })
  }

  dispatchMouseEvent(event: CdpMouseEventInput): void {
    void this.send("Input.dispatchMouseEvent", event).catch(() => undefined)
  }

  dispatchKeyEvent(event: object): void {
    void this.send("Input.dispatchKeyEvent", event).catch(() => undefined)
  }

  insertText(text: string): void {
    void this.send("Input.insertText", { text }).catch(() => undefined)
  }

  async detach(): Promise<void> {
    this.disposed = true
    const sessionId = this.sessionId
    this.sessionId = null
    this.screencast = null
    if (!sessionId || !this.connection.isReady) return
    await this.connection
      .send("Target.detachFromTarget", { sessionId })
      .catch(() => undefined)
  }

  /** The connection dropped underneath us; forget the stale session id. */
  markDisconnected(): void {
    this.sessionId = null
    this.screencast = null
  }

  private send<T = unknown>(method: string, params?: object): Promise<T> {
    if (!this.sessionId) {
      return Promise.reject(
        new CdpDisconnectedError("The page is not attached.")
      )
    }
    return this.connection.send<T>(method, params, this.sessionId)
  }
}
