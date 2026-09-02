import { useCallback, useRef, useState } from "react"
import type { PointerEvent as ReactPointerEvent } from "react"

function readStoredWidth(storageKey: string): number | null {
  if (typeof window === "undefined") return null
  const raw = window.localStorage.getItem(storageKey)
  if (raw === null) return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : null
}

function writeStoredWidth(storageKey: string, width: number): void {
  if (typeof window === "undefined") return
  window.localStorage.setItem(storageKey, String(width))
}

export interface UseResizableWidthOptions {
  /** localStorage key the persisted width is stored under. */
  readonly storageKey: string
  readonly defaultWidth: number
  readonly minWidth: number
  readonly maxWidth: number
  /**
   * Which edge of the host element carries the drag handle:
   *   - "left"  → panel grows leftward (right-anchored panels)
   *   - "right" → panel grows rightward (left-anchored panels)
   */
  readonly edge: "left" | "right"
}

export interface ResizableWidthHandlers {
  readonly onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void
  readonly onPointerMove: (event: ReactPointerEvent<HTMLElement>) => void
  readonly onPointerUp: (event: ReactPointerEvent<HTMLElement>) => void
  readonly onPointerCancel: (event: ReactPointerEvent<HTMLElement>) => void
}

/**
 * Width state for a side-anchored panel resized via a drag handle on the
 * specified edge. Width is read from localStorage on mount and persisted on
 * drag-end (not on every rAF tick — would otherwise be ~60 writes/sec).
 */
export function useResizableWidth(options: UseResizableWidthOptions): {
  readonly width: number
  readonly handlers: ResizableWidthHandlers
} {
  const { storageKey, defaultWidth, minWidth, maxWidth, edge } = options

  const clamp = useCallback(
    (value: number): number => {
      if (!Number.isFinite(value)) return defaultWidth
      return Math.max(minWidth, Math.min(maxWidth, value))
    },
    [defaultWidth, maxWidth, minWidth]
  )

  // No cross-tab subscription: panel width is per-window state.
  const [width, setWidth] = useState<number>(() =>
    clamp(readStoredWidth(storageKey) ?? defaultWidth)
  )

  const clampedWidth = clamp(width)

  const dragStateRef = useRef<{
    pointerId: number
    startX: number
    startWidth: number
    pending: number
    rafId: number | null
    target: HTMLElement
  } | null>(null)

  const releasePointer = useCallback((pointerId: number) => {
    const state = dragStateRef.current
    if (!state) return
    if (state.rafId !== null) cancelAnimationFrame(state.rafId)
    try {
      if (state.target.hasPointerCapture(pointerId)) {
        state.target.releasePointerCapture(pointerId)
      }
    } catch {
      // pointer may already be released; harmless.
    }
    document.body.style.removeProperty("cursor")
    document.body.style.removeProperty("user-select")
    dragStateRef.current = null
  }, [])

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      if (event.button !== 0) return
      event.preventDefault()
      event.stopPropagation()
      const target = event.currentTarget
      try {
        target.setPointerCapture(event.pointerId)
      } catch {
        return
      }
      document.body.style.cursor = "col-resize"
      document.body.style.userSelect = "none"
      dragStateRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startWidth: clampedWidth,
        pending: clampedWidth,
        rafId: null,
        target,
      }
    },
    [clampedWidth]
  )

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const state = dragStateRef.current
      if (!state || state.pointerId !== event.pointerId) return
      event.preventDefault()
      const delta =
        edge === "left"
          ? state.startX - event.clientX
          : event.clientX - state.startX
      state.pending = clamp(state.startWidth + delta)
      if (state.rafId !== null) return
      state.rafId = requestAnimationFrame(() => {
        const active = dragStateRef.current
        if (!active) return
        active.rafId = null
        setWidth(active.pending)
      })
    },
    [clamp, edge]
  )

  const onPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const state = dragStateRef.current
      if (!state || state.pointerId !== event.pointerId) return
      const finalWidth = clamp(state.pending)
      releasePointer(event.pointerId)
      // Commit once at drag-end to avoid 60Hz localStorage writes.
      writeStoredWidth(storageKey, finalWidth)
      setWidth(finalWidth)
    },
    [clamp, releasePointer, storageKey]
  )

  const onPointerCancel = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const state = dragStateRef.current
      if (!state || state.pointerId !== event.pointerId) return
      // Don't persist a cancelled drag; revert to the start width.
      releasePointer(event.pointerId)
      setWidth(state.startWidth)
    },
    [releasePointer]
  )

  return {
    width: clampedWidth,
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel },
  }
}
