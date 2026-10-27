import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import {
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react"

import { cn } from "@/lib/utils"
import type { PixelRect } from "./pixels"

type Zoom = "fit" | number
interface Point {
  x: number
  y: number
}
interface Gesture {
  pointerId: number
  element: HTMLDivElement
  mode: "pan" | "select"
  client: Point
  start: Point
  scroll: Point
}

export interface ComparisonViewportProps {
  width: number
  height: number
  panes: { id: string; label: string; content: ReactNode }[]
  focus?: PixelRect | null
  interaction?: "pan" | "select"
  onSelect?: (rect: PixelRect) => void
}

function rectangle(start: Point, end: Point): PixelRect {
  const x = Math.floor(Math.min(start.x, end.x))
  const y = Math.floor(Math.min(start.y, end.y))
  return {
    x,
    y,
    width: Math.ceil(Math.max(start.x, end.x)) - x,
    height: Math.ceil(Math.max(start.y, end.y)) - y,
  }
}

export function ComparisonViewport({
  width,
  height,
  panes,
  focus,
  interaction = "pan",
  onSelect,
}: ComparisonViewportProps) {
  const viewports = useRef(new Map<string, HTMLDivElement>())
  const sources = useRef(new Map<string, HTMLDivElement>())
  const gesture = useRef<Gesture | null>(null)
  const scroll = useRef<Point>({ x: 0, y: 0 })
  const pendingCenter = useRef<(Point & { zoom: Zoom }) | null>(null)
  const lastFocus = useRef<PixelRect | null>(null)
  const [zoom, setZoom] = useState<Zoom>("fit")
  const [viewport, setViewport] = useState({
    width: 0,
    height: 0,
    paneWidth: 0,
  })
  const [dragging, setDragging] = useState(false)
  const [selection, setSelection] = useState<PixelRect | null>(null)
  const paneIds = panes.map((pane) => pane.id).join(":")
  const fitScale = Math.min(
    1,
    (viewport.width || width) / width,
    (viewport.height || height) / height
  )
  const scale = zoom === "fit" ? fitScale : zoom

  useLayoutEffect(() => {
    const first = viewports.current.values().next().value
    if (!first) return
    const measure = () => {
      const next = {
        width: first.clientWidth,
        height: first.clientHeight,
        paneWidth: first.offsetWidth || first.clientWidth,
      }
      setViewport((previous) =>
        previous.width === next.width &&
        previous.height === next.height &&
        previous.paneWidth === next.paneWidth
          ? previous
          : next
      )
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(first)
    return () => observer.disconnect()
  }, [paneIds])

  useLayoutEffect(() => {
    if (!focus) {
      lastFocus.current = null
      return
    }
    if (!viewport.width || !viewport.height || lastFocus.current === focus)
      return
    lastFocus.current = focus
    const next = Math.min(
      4,
      Math.max(
        fitScale,
        Math.min(
          (viewport.width * 0.8) / Math.max(1, focus.width),
          (viewport.height * 0.8) / Math.max(1, focus.height)
        )
      )
    )
    pendingCenter.current = {
      x: focus.x + focus.width / 2,
      y: focus.y + focus.height / 2,
      zoom: next,
    }
    setZoom(next)
  }, [focus, viewport.width, viewport.height, fitScale])

  useLayoutEffect(() => {
    const center = pendingCenter.current
    if (center && center.zoom !== zoom) return
    for (const element of viewports.current.values()) {
      element.scrollLeft = center
        ? Math.max(0, center.x * scale - element.clientWidth / 2)
        : scroll.current.x * scale
      element.scrollTop = center
        ? Math.max(0, center.y * scale - element.clientHeight / 2)
        : scroll.current.y * scale
    }
    pendingCenter.current = null
  }, [zoom, scale, paneIds, focus])

  function synchronize(element: HTMLDivElement) {
    scroll.current = {
      x: element.scrollLeft / scale,
      y: element.scrollTop / scale,
    }
    for (const other of viewports.current.values()) {
      if (other === element) continue
      if (Math.abs(other.scrollLeft - element.scrollLeft) > 1)
        other.scrollLeft = element.scrollLeft
      if (Math.abs(other.scrollTop - element.scrollTop) > 1)
        other.scrollTop = element.scrollTop
    }
  }

  function changeZoom(next: Zoom) {
    const first = viewports.current.entries().next().value
    if (first) {
      const [id, element] = first
      const source = sources.current.get(id)
      if (source) {
        const bounds = source.getBoundingClientRect()
        const view = element.getBoundingClientRect()
        pendingCenter.current = {
          x: Math.max(
            0,
            Math.min(
              width,
              (view.left + element.clientWidth / 2 - bounds.left) / scale
            )
          ),
          y: Math.max(
            0,
            Math.min(
              height,
              (view.top + element.clientHeight / 2 - bounds.top) / scale
            )
          ),
          zoom: next,
        }
      }
    }
    setZoom(next)
  }

  function point(event: ReactPointerEvent<HTMLDivElement>, id: string): Point {
    const bounds = sources.current.get(id)!.getBoundingClientRect()
    return {
      x: Math.max(0, Math.min(width, (event.clientX - bounds.left) / scale)),
      y: Math.max(0, Math.min(height, (event.clientY - bounds.top) / scale)),
    }
  }

  function finishGesture(event: ReactPointerEvent<HTMLDivElement>, id: string) {
    const current = gesture.current
    if (!current || current.pointerId !== event.pointerId) return
    gesture.current = null
    setDragging(false)
    setSelection(null)
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId)
    if (current.mode !== "select" || event.type === "pointercancel") return
    const selected = rectangle(current.start, point(event, id))
    if (
      selected.width >= 4 &&
      selected.height >= 4 &&
      Math.hypot(
        event.clientX - current.client.x,
        event.clientY - current.client.y
      ) >= 4
    )
      onSelect?.(selected)
  }

  return (
    <div className="min-w-0 overflow-hidden rounded-md border border-default bg-surface-level-2">
      <div className="flex flex-wrap items-center justify-between gap-space-2 border-b border-default p-space-2">
        <GroupedTabs
          size="sm"
          value={String(zoom)}
          onChange={(value) =>
            changeZoom(value === "fit" ? "fit" : Number(value))
          }
          options={[
            { value: "fit", display: "Fit" },
            { value: "1", display: "100%" },
            { value: "2", display: "200%" },
            { value: "4", display: "400%" },
          ]}
        />
        <span className="px-space-1 text-xs text-secondary" role="status">
          {Math.round(scale * 100)}% ·{" "}
          {panes.length > 1 ? "Synced viewports" : "Drag to pan"}
        </span>
      </div>
      <div
        className="grid min-w-0 divide-x divide-default"
        style={{
          gridTemplateColumns: `repeat(${panes.length}, minmax(0, 1fr))`,
        }}
      >
        {panes.map((pane) => (
          <div key={pane.id} className="min-w-0">
            <div className="border-b border-default px-space-3 py-space-2 text-xs font-medium text-secondary">
              {pane.label}
            </div>
            <div
              ref={(element) => {
                if (element) viewports.current.set(pane.id, element)
                else viewports.current.delete(pane.id)
              }}
              role="region"
              aria-label={`${pane.label} screenshot viewport`}
              aria-description={
                interaction === "select"
                  ? "Drag to select an area to ignore. Escape cancels the selection."
                  : "Drag to pan or use the arrow keys to scroll. Plus and minus zoom. Zero fits the screenshot."
              }
              tabIndex={0}
              className={cn(
                "focus-visible:outline-info overflow-auto overscroll-contain outline-offset-2 focus-visible:outline-2",
                interaction === "select"
                  ? "cursor-crosshair"
                  : dragging
                    ? "cursor-grabbing"
                    : "cursor-grab"
              )}
              style={{
                height: `clamp(180px, ${((viewport.paneWidth || width) * height) / width}px, min(60vh, 560px))`,
                touchAction: "none",
              }}
              onScroll={(event) => synchronize(event.currentTarget)}
              onKeyDown={(event) => {
                if (event.ctrlKey || event.metaKey || event.altKey) return
                if (event.key === "Escape") {
                  const current = gesture.current
                  if (current?.element.hasPointerCapture(current.pointerId))
                    current.element.releasePointerCapture(current.pointerId)
                  gesture.current = null
                  setSelection(null)
                  setDragging(false)
                } else if (event.target !== event.currentTarget) return
                else if (event.key === "0") {
                  event.preventDefault()
                  changeZoom("fit")
                } else if (event.key === "+" || event.key === "=") {
                  event.preventDefault()
                  changeZoom(Math.min(4, scale * 2))
                } else if (event.key === "-") {
                  event.preventDefault()
                  changeZoom(Math.max(fitScale, scale / 2))
                }
              }}
              onPointerDown={(event) => {
                if (event.button !== 0 || gesture.current) return
                if (
                  interaction === "pan" &&
                  event.target instanceof Element &&
                  event.target.closest("button, a, input, select, textarea")
                )
                  return
                event.preventDefault()
                event.currentTarget.focus({ preventScroll: true })
                event.currentTarget.setPointerCapture(event.pointerId)
                const start = point(event, pane.id)
                gesture.current = {
                  pointerId: event.pointerId,
                  element: event.currentTarget,
                  mode: interaction,
                  client: { x: event.clientX, y: event.clientY },
                  start,
                  scroll: {
                    x: event.currentTarget.scrollLeft,
                    y: event.currentTarget.scrollTop,
                  },
                }
                setDragging(true)
                if (interaction === "select")
                  setSelection(rectangle(start, start))
              }}
              onPointerMove={(event) => {
                const current = gesture.current
                if (!current || current.pointerId !== event.pointerId) return
                if (current.mode === "select")
                  setSelection(rectangle(current.start, point(event, pane.id)))
                else {
                  event.currentTarget.scrollLeft =
                    current.scroll.x + current.client.x - event.clientX
                  event.currentTarget.scrollTop =
                    current.scroll.y + current.client.y - event.clientY
                  synchronize(event.currentTarget)
                }
              }}
              onPointerUp={(event) => finishGesture(event, pane.id)}
              onPointerCancel={(event) => finishGesture(event, pane.id)}
              onLostPointerCapture={() => {
                gesture.current = null
                setDragging(false)
                setSelection(null)
              }}
            >
              <div
                ref={(element) => {
                  if (element) sources.current.set(pane.id, element)
                  else sources.current.delete(pane.id)
                }}
                className="relative mx-auto select-none"
                style={{ width: width * scale, height: height * scale }}
              >
                {pane.content}
                {selection && (
                  <div
                    className="pointer-events-none absolute rounded-sm border-2 border-warning bg-warning/10"
                    style={{
                      left: selection.x * scale,
                      top: selection.y * scale,
                      width: selection.width * scale,
                      height: selection.height * scale,
                    }}
                  />
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
