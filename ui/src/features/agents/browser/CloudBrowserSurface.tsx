import { useEffect, useLayoutEffect, useRef, useState } from "react"
import type {
  ClipboardEvent as ReactClipboardEvent,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
  WheelEvent as ReactWheelEvent,
} from "react"

import type { BrowserTabState } from "@/features/agents/browser/browserTabStore"
import type {
  BrowserViewport,
  BrowserViewportSize,
} from "@/features/agents/browser/browserViewport"
import type { CdpScreencastFrame } from "@/features/agents/browser/cdp/cdpPageSession"
import type { CloudBrowserSession } from "@/features/agents/browser/cloudBrowserSession"
import { BrowserViewportFrame } from "@/features/agents/browser/BrowserViewportFrame"
import { normalizeZoomFactor } from "@/features/agents/browser/browserViewport"
import {
  cdpKeyEvents,
  cdpModifiers,
} from "@/features/agents/browser/cdp/keyboardCodes"
import { cn } from "@/lib/utils"

const MAX_DEVICE_SCALE_FACTOR = 2
const MAX_FRAME_EDGE = 3840
const MOUSE_BUTTONS = ["left", "middle", "right", "back", "forward"] as const

interface Props {
  readonly session: CloudBrowserSession
  readonly tab: BrowserTabState
  readonly visible: boolean
  readonly onViewportChange: (viewport: BrowserViewport) => Promise<void>
}

interface PageMetrics {
  /** CSS pixel size the page is told it has. */
  readonly cssWidth: number
  readonly cssHeight: number
  readonly deviceScaleFactor: number
}

function isApplePlatform(): boolean {
  return (
    typeof navigator !== "undefined" &&
    /Mac|iPhone|iPad/.test(navigator.platform)
  )
}

function decodeJpeg(base64: string): Promise<ImageBitmap> {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return createImageBitmap(new Blob([bytes], { type: "image/jpeg" }))
}

/**
 * Paints the sandbox browser's screencast into a canvas and forwards pointer,
 * wheel, and keyboard input over CDP. The page is emulated at the size the
 * frame gives it, so the picture is pixel-accurate for the panel.
 */
export function CloudBrowserSurface({
  session,
  tab,
  visible,
  onViewportChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const metricsRef = useRef<PageMetrics | null>(null)
  const latestFrameRef = useRef<CdpScreencastFrame | null>(null)
  const decodingRef = useRef(false)
  const pressedButtonsRef = useRef(0)
  const [containerSize, setContainerSize] = useState<BrowserViewportSize>({
    width: 1,
    height: 1,
  })
  const [hasFrame, setHasFrame] = useState(false)
  const tabId = tab.tabId
  const zoom = normalizeZoomFactor(tab.zoomFactor)
  const swapMetaForControl = isApplePlatform()

  useLayoutEffect(() => {
    const element = containerRef.current
    if (!element) return
    const update = () => {
      const rect = element.getBoundingClientRect()
      setContainerSize((current) => {
        const width = Math.max(1, Math.round(rect.width))
        const height = Math.max(1, Math.round(rect.height))
        return current.width === width && current.height === height
          ? current
          : { width, height }
      })
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  // Paint the most recent frame; decoding is async, so frames that arrive
  // while one is decoding simply replace the pending one.
  useEffect(() => {
    let disposed = false
    function paintLatest(): void {
      const frame = latestFrameRef.current
      const canvas = canvasRef.current
      if (disposed || !frame || !canvas || decodingRef.current) return
      latestFrameRef.current = null
      decodingRef.current = true
      void decodeJpeg(frame.data)
        .then((bitmap) => {
          if (disposed) {
            bitmap.close()
            return
          }
          const context = canvas.getContext("2d")
          if (!context) return
          if (
            canvas.width !== bitmap.width ||
            canvas.height !== bitmap.height
          ) {
            canvas.width = bitmap.width
            canvas.height = bitmap.height
          }
          context.drawImage(bitmap, 0, 0)
          bitmap.close()
          setHasFrame(true)
        })
        .catch(() => undefined)
        .finally(() => {
          decodingRef.current = false
          if (latestFrameRef.current) paintLatest()
        })
    }
    const unsubscribe = session.onFrame(tabId, (frame) => {
      latestFrameRef.current = frame
      paintLatest()
    })
    return () => {
      disposed = true
      unsubscribe()
    }
  }, [session, tabId])

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 h-full w-full overflow-hidden bg-muted/35"
    >
      <BrowserViewportFrame
        containerSize={containerSize}
        viewport={tab.viewport}
        zoomFactor={zoom}
        onViewportChange={onViewportChange}
      >
        {(layout) => (
          <ScreencastCanvas
            canvasRef={canvasRef}
            metricsRef={metricsRef}
            pressedButtonsRef={pressedButtonsRef}
            session={session}
            tabId={tabId}
            attached={tab.attached}
            visible={visible}
            hasFrame={hasFrame}
            zoom={zoom}
            swapMetaForControl={swapMetaForControl}
            layout={layout}
            cssViewport={
              tab.viewport.mode === "fill"
                ? {
                    width: Math.max(1, Math.round(layout.viewportWidth / zoom)),
                    height: Math.max(
                      1,
                      Math.round(layout.viewportHeight / zoom)
                    ),
                  }
                : { width: tab.viewport.width, height: tab.viewport.height }
            }
          />
        )}
      </BrowserViewportFrame>
    </div>
  )
}

function ScreencastCanvas(props: {
  readonly canvasRef: React.RefObject<HTMLCanvasElement | null>
  readonly metricsRef: React.RefObject<PageMetrics | null>
  readonly pressedButtonsRef: React.RefObject<number>
  readonly session: CloudBrowserSession
  readonly tabId: string
  readonly attached: boolean
  readonly visible: boolean
  readonly hasFrame: boolean
  readonly zoom: number
  readonly swapMetaForControl: boolean
  readonly layout: {
    readonly viewportX: number
    readonly viewportY: number
    readonly viewportWidth: number
    readonly viewportHeight: number
    readonly viewportScale: number
    readonly fillsPanel: boolean
  }
  readonly cssViewport: BrowserViewportSize
}) {
  const {
    canvasRef,
    metricsRef,
    pressedButtonsRef,
    session,
    tabId,
    visible,
    hasFrame,
    zoom,
    swapMetaForControl,
    layout,
    cssViewport,
  } = props
  const devicePixelRatio =
    typeof window === "undefined"
      ? 1
      : Math.min(MAX_DEVICE_SCALE_FACTOR, window.devicePixelRatio || 1)
  const deviceScaleFactor = devicePixelRatio * zoom
  const cssWidth = cssViewport.width
  const cssHeight = cssViewport.height

  // Emulate the page at the frame's size and stream it while visible. Any
  // change to size, zoom, or visibility restarts the screencast so frames
  // always match the canvas.
  useEffect(() => {
    if (!visible) return
    let cancelled = false
    void (async () => {
      try {
        const page = await session.ensurePage(tabId)
        if (cancelled) return
        metricsRef.current = { cssWidth, cssHeight, deviceScaleFactor }
        await page.setDeviceMetrics({
          width: cssWidth,
          height: cssHeight,
          deviceScaleFactor,
        })
        if (cancelled) return
        await page.startScreencast({
          maxWidth: Math.min(
            MAX_FRAME_EDGE,
            Math.ceil(cssWidth * deviceScaleFactor)
          ),
          maxHeight: Math.min(
            MAX_FRAME_EDGE,
            Math.ceil(cssHeight * deviceScaleFactor)
          ),
        })
      } catch {
        // The connection state store surfaces attach failures to the user.
      }
    })()
    return () => {
      cancelled = true
      const page = session.hasTarget(tabId) ? session.ensurePage(tabId) : null
      void page
        ?.then((attached) => attached.stopScreencast())
        .catch(() => undefined)
    }
  }, [
    cssHeight,
    cssWidth,
    deviceScaleFactor,
    metricsRef,
    session,
    tabId,
    visible,
  ])

  const pagePoint = (event: { clientX: number; clientY: number }) => {
    const canvas = canvasRef.current
    if (!canvas) return null
    const rect = canvas.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return null
    return {
      x: ((event.clientX - rect.left) / rect.width) * cssWidth,
      y: ((event.clientY - rect.top) / rect.height) * cssHeight,
    }
  }

  const withPage = (
    use: (page: Awaited<ReturnType<CloudBrowserSession["ensurePage"]>>) => void
  ) => {
    void session
      .ensurePage(tabId)
      .then(use)
      .catch(() => undefined)
  }

  const handlePointer = (
    type: "mousePressed" | "mouseReleased" | "mouseMoved",
    event: ReactPointerEvent<HTMLCanvasElement>
  ) => {
    const point = pagePoint(event)
    if (!point) return
    if (type === "mousePressed") {
      event.currentTarget.focus({ preventScroll: true })
      pressedButtonsRef.current = event.buttons
    } else if (type === "mouseReleased") {
      pressedButtonsRef.current = event.buttons
    }
    const button =
      type === "mouseMoved" && event.buttons === 0
        ? "none"
        : (MOUSE_BUTTONS[event.button] ?? "none")
    withPage((page) =>
      page.dispatchMouseEvent({
        type,
        x: point.x,
        y: point.y,
        button,
        buttons: event.buttons,
        clickCount: type === "mouseMoved" ? 0 : Math.max(1, event.detail || 1),
        modifiers: cdpModifiers(event, swapMetaForControl),
      })
    )
  }

  const handleWheel = (event: ReactWheelEvent<HTMLCanvasElement>) => {
    const point = pagePoint(event)
    if (!point) return
    const lineHeight = 16
    const scale =
      event.deltaMode === 1 ? lineHeight : event.deltaMode === 2 ? cssHeight : 1
    withPage((page) =>
      page.dispatchMouseEvent({
        type: "mouseWheel",
        x: point.x,
        y: point.y,
        deltaX: (event.deltaX * scale) / (zoom * layout.viewportScale),
        deltaY: (event.deltaY * scale) / (zoom * layout.viewportScale),
        modifiers: cdpModifiers(event, swapMetaForControl),
      })
    )
  }

  const handleKey = (
    phase: "down" | "up",
    event: ReactKeyboardEvent<HTMLCanvasElement>
  ) => {
    if (event.nativeEvent.isComposing) return
    const events = cdpKeyEvents(event, swapMetaForControl)
    event.preventDefault()
    withPage((page) =>
      page.dispatchKeyEvent(phase === "down" ? events.down : events.up)
    )
  }

  const handlePaste = (event: ReactClipboardEvent<HTMLCanvasElement>) => {
    const text = event.clipboardData.getData("text/plain")
    if (!text) return
    event.preventDefault()
    withPage((page) => page.insertText(text))
  }

  return (
    <canvas
      ref={canvasRef}
      tabIndex={0}
      aria-label="Browser page"
      data-cloud-browser-canvas={tabId}
      className={cn(
        "absolute block bg-white outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        !layout.fillsPanel && "shadow-sm ring-1 ring-border/70",
        !hasFrame && "opacity-0"
      )}
      style={{
        left: layout.viewportX,
        top: layout.viewportY,
        width: layout.viewportWidth,
        height: layout.viewportHeight,
        cursor: "default",
        touchAction: "none",
      }}
      onPointerDown={(event) => handlePointer("mousePressed", event)}
      onPointerUp={(event) => handlePointer("mouseReleased", event)}
      onPointerMove={(event) => handlePointer("mouseMoved", event)}
      onWheel={handleWheel}
      onKeyDown={(event) => handleKey("down", event)}
      onKeyUp={(event) => handleKey("up", event)}
      onPaste={handlePaste}
      onContextMenu={(event) => event.preventDefault()}
    />
  )
}
