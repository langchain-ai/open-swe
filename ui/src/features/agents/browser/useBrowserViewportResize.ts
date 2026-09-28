import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react"
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react"

import {
  type BrowserViewport,
  type BrowserViewportResizeDirection,
  type BrowserViewportSize,
  browserViewportKey,
  normalizeZoomFactor,
  resizeBrowserViewportFromRail,
  resizeFreeformViewport,
  resolveBrowserDeviceViewportArea,
  resolveBrowserDeviceViewportLayout,
  resolveBrowserViewportLayout,
} from "@/features/agents/browser/browserViewport"

interface ViewportDrag extends BrowserViewportSize {
  readonly sourceKey: string
  readonly direction: BrowserViewportResizeDirection
}

const KEYBOARD_RESIZE_COMMIT_DELAY_MS = 150

/**
 * Drag and keyboard resizing of a fixed viewport. The in-progress size is
 * local state so the frame follows the pointer immediately; the committed
 * size goes through `onCommit`, which the host applies to the page.
 */
export function useBrowserViewportResize(options: {
  readonly viewport: BrowserViewport
  readonly zoomFactor: number
  readonly containerSize: BrowserViewportSize
  readonly deviceToolbarVisible: boolean
  readonly aspectRatio: number | null
  readonly onCommit: (viewport: BrowserViewport) => Promise<void>
}) {
  const {
    viewport,
    zoomFactor,
    containerSize,
    deviceToolbarVisible,
    aspectRatio,
    onCommit,
  } = options
  const dragCleanupRef = useRef<(() => void) | null>(null)
  const dragVersionRef = useRef(0)
  const keyboardCommitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  )
  const keyboardViewportRef = useRef<ViewportDrag | null>(null)
  const onCommitRef = useRef(onCommit)
  const [dragViewport, setDragViewport] = useState<ViewportDrag | null>(null)
  const sourceViewportKey = browserViewportKey(viewport)
  const sourceViewportKeyRef = useRef(sourceViewportKey)
  // Refs mirror the latest props for the window-level drag listeners, which
  // outlive the render that installed them.
  useLayoutEffect(() => {
    onCommitRef.current = onCommit
    sourceViewportKeyRef.current = sourceViewportKey
  }, [onCommit, sourceViewportKey])
  const activeDrag =
    dragViewport?.sourceKey === sourceViewportKey ? dragViewport : null
  const effectiveViewport: BrowserViewport = activeDrag
    ? { mode: "freeform", width: activeDrag.width, height: activeDrag.height }
    : viewport
  const zoom = normalizeZoomFactor(zoomFactor)
  const viewportContainerSize = deviceToolbarVisible
    ? resolveBrowserDeviceViewportArea(containerSize)
    : containerSize
  const layout =
    deviceToolbarVisible && effectiveViewport.mode !== "fill"
      ? resolveBrowserDeviceViewportLayout(
          containerSize,
          effectiveViewport,
          zoom
        )
      : resolveBrowserViewportLayout(containerSize, effectiveViewport, zoom)

  useEffect(
    () => () => {
      dragVersionRef.current += 1
      dragCleanupRef.current?.()
      if (keyboardCommitTimerRef.current !== null) {
        clearTimeout(keyboardCommitTimerRef.current)
      }
      keyboardCommitTimerRef.current = null
      keyboardViewportRef.current = null
    },
    []
  )

  useEffect(() => {
    const pending = keyboardViewportRef.current
    if (!pending || pending.sourceKey === sourceViewportKey) return
    if (keyboardCommitTimerRef.current !== null) {
      clearTimeout(keyboardCommitTimerRef.current)
      keyboardCommitTimerRef.current = null
    }
    keyboardViewportRef.current = null
  }, [sourceViewportKey])

  const commitViewportChange = useCallback((next: BrowserViewport) => {
    dragVersionRef.current += 1
    dragCleanupRef.current?.()
    if (keyboardCommitTimerRef.current !== null) {
      clearTimeout(keyboardCommitTimerRef.current)
      keyboardCommitTimerRef.current = null
    }
    keyboardViewportRef.current = null
    setDragViewport(null)
    return onCommitRef.current(next)
  }, [])

  const clearDrag = () => setDragViewport(null)
  const commitDrag = (next: BrowserViewport) => {
    const version = ++dragVersionRef.current
    const clearIfCurrent = () => {
      if (dragVersionRef.current === version) clearDrag()
    }
    void onCommitRef.current(next).then(clearIfCurrent, clearIfCurrent)
  }

  const handleResizeKeyDown = (
    direction: BrowserViewportResizeDirection,
    event: ReactKeyboardEvent<HTMLButtonElement>
  ) => {
    if (effectiveViewport.mode === "fill") return
    const controlsWidth =
      direction.includes("east") || direction.includes("west")
    const controlsHeight =
      direction.includes("north") || direction.includes("south")
    const step = (event.shiftKey ? 50 : 10) * zoom
    const delta =
      event.key === "ArrowLeft" && controlsWidth
        ? { x: -step, y: 0 }
        : event.key === "ArrowRight" && controlsWidth
          ? { x: step, y: 0 }
          : event.key === "ArrowUp" && controlsHeight
            ? { x: 0, y: -step }
            : event.key === "ArrowDown" && controlsHeight
              ? { x: 0, y: step }
              : null
    if (!delta) return
    event.preventDefault()
    event.stopPropagation()
    const pending = keyboardViewportRef.current
    const base =
      pending?.sourceKey === sourceViewportKey ? pending : effectiveViewport
    const next = resizeFreeformViewport(
      base,
      delta,
      zoom,
      direction,
      aspectRatio ?? undefined
    )
    if (next.width === base.width && next.height === base.height) return
    const keyboardViewport = {
      sourceKey: sourceViewportKey,
      ...next,
      direction,
    }
    keyboardViewportRef.current = keyboardViewport
    setDragViewport(keyboardViewport)
    if (keyboardCommitTimerRef.current !== null) {
      clearTimeout(keyboardCommitTimerRef.current)
    }
    keyboardCommitTimerRef.current = setTimeout(() => {
      keyboardCommitTimerRef.current = null
      const latest = keyboardViewportRef.current
      if (!latest || latest.sourceKey !== sourceViewportKeyRef.current) return
      keyboardViewportRef.current = null
      commitDrag({
        mode: "freeform",
        width: latest.width,
        height: latest.height,
      })
    }, KEYBOARD_RESIZE_COMMIT_DELAY_MS)
  }

  const handleResizePointerDown = (
    direction: BrowserViewportResizeDirection,
    event: ReactPointerEvent<HTMLButtonElement>
  ) => {
    if (effectiveViewport.mode === "fill") return
    event.preventDefault()
    event.stopPropagation()
    if (keyboardCommitTimerRef.current !== null) {
      clearTimeout(keyboardCommitTimerRef.current)
      keyboardCommitTimerRef.current = null
    }
    keyboardViewportRef.current = null
    dragCleanupRef.current?.()
    dragVersionRef.current += 1
    const pointerId = event.pointerId
    const target = event.currentTarget
    const startX = event.clientX
    const startY = event.clientY
    const startWidth = effectiveViewport.width
    const startHeight = effectiveViewport.height
    const dragZoomFactor = zoom * layout.viewportScale
    let latest = { width: startWidth, height: startHeight }
    setDragViewport({
      sourceKey: sourceViewportKey,
      width: startWidth,
      height: startHeight,
      direction,
    })
    try {
      target.setPointerCapture(pointerId)
    } catch {
      // The window listeners below keep the drag working without capture.
    }
    const sourceChanged = () =>
      sourceViewportKeyRef.current !== sourceViewportKey
    const move = (moveEvent: PointerEvent) => {
      if (moveEvent.pointerId !== pointerId) return
      if (sourceChanged()) {
        cleanup()
        dragVersionRef.current += 1
        clearDrag()
        return
      }
      moveEvent.preventDefault()
      const { width, height } = resizeBrowserViewportFromRail(
        { width: startWidth, height: startHeight },
        { x: moveEvent.clientX - startX, y: moveEvent.clientY - startY },
        viewportContainerSize,
        dragZoomFactor,
        direction,
        aspectRatio ?? undefined
      )
      latest = { width, height }
      setDragViewport({
        sourceKey: sourceViewportKey,
        width,
        height,
        direction,
      })
    }
    function cleanup() {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", finish)
      window.removeEventListener("pointercancel", cancel)
      dragCleanupRef.current = null
      try {
        target.releasePointerCapture(pointerId)
      } catch {
        // Capture may already have been released on pointerup.
      }
    }
    function finish(upEvent: PointerEvent) {
      if (upEvent.pointerId !== pointerId) return
      cleanup()
      if (
        sourceChanged() ||
        (latest.width === startWidth && latest.height === startHeight)
      ) {
        clearDrag()
        return
      }
      commitDrag({
        mode: "freeform",
        width: latest.width,
        height: latest.height,
      })
    }
    function cancel(cancelEvent: PointerEvent) {
      if (cancelEvent.pointerId !== pointerId) return
      cleanup()
      dragVersionRef.current += 1
      clearDrag()
    }
    dragCleanupRef.current = cleanup
    window.addEventListener("pointermove", move, { passive: false })
    window.addEventListener("pointerup", finish)
    window.addEventListener("pointercancel", cancel)
  }

  return {
    activeDrag,
    commitViewportChange,
    effectiveViewport,
    handleResizeKeyDown,
    handleResizePointerDown,
    layout,
  }
}
