import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import {
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react"

import { cn } from "@/lib/utils"

type Zoom = "fit" | number
interface Point {
  x: number
  y: number
}
interface Gesture {
  pointerId: number
  element: HTMLDivElement
  client: Point
  scroll: Point
}

export interface ComparisonViewportProps {
  width: number
  height: number
  panes: { id: string; label: string; content: ReactNode }[]
}

export function ComparisonViewport({
  width,
  height,
  panes,
}: ComparisonViewportProps) {
  const viewports = useRef(new Map<string, HTMLDivElement>())
  const sources = useRef(new Map<string, HTMLDivElement>())
  const gesture = useRef<Gesture | null>(null)
  const scroll = useRef<Point>({ x: 0, y: 0 })
  const pendingCenter = useRef<(Point & { zoom: Zoom }) | null>(null)
  const [zoom, setZoom] = useState<Zoom>("fit")
  const [viewport, setViewport] = useState({
    width: 0,
    height: 0,
    paneWidth: 0,
  })
  const [dragging, setDragging] = useState(false)
  const paneIds = panes.map((pane) => pane.id).join(":")
  const fitScale = Math.min(
    1,
    (viewport.width || width) / width,
    (viewport.height || height) / height
  )
  const scale = zoom === "fit" ? fitScale : zoom
  const canPan =
    width * scale > viewport.width + 1 || height * scale > viewport.height + 1

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
  }, [zoom, scale, paneIds])

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

  function finishGesture(event: ReactPointerEvent<HTMLDivElement>) {
    if (gesture.current?.pointerId !== event.pointerId) return
    gesture.current = null
    setDragging(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId)
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
          {Math.round(scale * 100)}%{canPan ? " · Drag to pan" : ""}
        </span>
      </div>
      <div
        className={cn(
          "grid min-w-0 divide-y divide-default sm:divide-y-0",
          panes.length > 1 && "sm:grid-cols-2 sm:divide-x"
        )}
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
              aria-description={`${canPan ? "Drag to pan or use the arrow keys to scroll. " : ""}Plus and minus zoom. Zero fits the screenshot.`}
              tabIndex={0}
              className={cn(
                "overflow-auto overscroll-contain bg-surface-level-3 outline-offset-2 focus-visible:outline-2 focus-visible:outline-[var(--border-focus)]",
                canPan && (dragging ? "cursor-grabbing" : "cursor-grab")
              )}
              style={{
                height: `clamp(180px, ${((viewport.paneWidth || width) * height) / width}px, min(60vh, 560px))`,
                touchAction: canPan ? "none" : "auto",
              }}
              onScroll={(event) => synchronize(event.currentTarget)}
              onKeyDown={(event) => {
                if (event.ctrlKey || event.metaKey || event.altKey) return
                if (event.key === "Escape") {
                  const current = gesture.current
                  if (current?.element.hasPointerCapture(current.pointerId))
                    current.element.releasePointerCapture(current.pointerId)
                  gesture.current = null
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
                if (!canPan || event.button !== 0 || gesture.current) return
                if (
                  event.target instanceof Element &&
                  event.target.closest("button, a, input, select, textarea")
                )
                  return
                event.preventDefault()
                event.currentTarget.focus({ preventScroll: true })
                event.currentTarget.setPointerCapture(event.pointerId)
                gesture.current = {
                  pointerId: event.pointerId,
                  element: event.currentTarget,
                  client: { x: event.clientX, y: event.clientY },
                  scroll: {
                    x: event.currentTarget.scrollLeft,
                    y: event.currentTarget.scrollTop,
                  },
                }
                setDragging(true)
              }}
              onPointerMove={(event) => {
                const current = gesture.current
                if (!current || current.pointerId !== event.pointerId) return
                event.currentTarget.scrollLeft =
                  current.scroll.x + current.client.x - event.clientX
                event.currentTarget.scrollTop =
                  current.scroll.y + current.client.y - event.clientY
                synchronize(event.currentTarget)
              }}
              onPointerUp={finishGesture}
              onPointerCancel={finishGesture}
              onLostPointerCapture={() => {
                gesture.current = null
                setDragging(false)
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
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
