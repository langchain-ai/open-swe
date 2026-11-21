import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Slider } from "@langchain/macaw-components/Slider"
import { Text } from "@langchain/macaw-components/Text"
import { cn } from "@langchain/macaw-components/utils/cn"
import { ArrowsOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowsOut"
import { MagnifyingGlassMinusIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlassMinus"
import { MagnifyingGlassPlusIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlassPlus"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react"

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

/** Screenshot panes that zoom and pan together. */
export function ComparisonViewport({
  width,
  height,
  panes,
}: ComparisonViewportProps) {
  const viewports = useRef(new Map<string, HTMLDivElement>())
  const gesture = useRef<Gesture | null>(null)
  const dragged = useRef(false)
  const gestureScale = useRef(1)
  const scroll = useRef<Point>({ x: 0, y: 0 })
  const pendingAnchor = useRef<(Point & { offset: Point }) | null>(null)
  const requestedScale = useRef<number | null>(null)
  const opener = useRef<HTMLButtonElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [firstViewport, setFirstViewport] = useState<HTMLDivElement | null>(
    null
  )
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
  const minimumScale = Math.min(fitScale, 0.1)
  const maximumScale = Math.max(16, scale)
  const firstPaneId = panes[0]?.id
  const registerViewport = useCallback(
    (element: HTMLDivElement | null) => {
      if (firstPaneId !== undefined) {
        if (element) viewports.current.set(firstPaneId, element)
        else viewports.current.delete(firstPaneId)
      }
      setFirstViewport(element)
    },
    [firstPaneId]
  )

  useLayoutEffect(() => {
    const first = firstViewport
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
  }, [firstViewport])

  useLayoutEffect(() => {
    const anchor = pendingAnchor.current
    for (const element of viewports.current.values()) {
      element.scrollLeft = anchor
        ? anchor.x * scale +
          Math.max(0, (element.clientWidth - width * scale) / 2) -
          anchor.offset.x * element.clientWidth
        : scroll.current.x * scale
      element.scrollTop = anchor
        ? anchor.y * scale - anchor.offset.y * element.clientHeight
        : scroll.current.y * scale
    }
    pendingAnchor.current = null
    requestedScale.current = null
    const first = viewports.current.values().next().value
    if (first)
      scroll.current = {
        x: first.scrollLeft / scale,
        y: first.scrollTop / scale,
      }
  }, [zoom, scale, paneIds, firstViewport, width])

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

  const changeZoom = useCallback(
    (next: Zoom, element?: HTMLDivElement, point?: Point) => {
      const target = element ?? viewports.current.values().next().value
      const nextScale = next === "fit" ? fitScale : Math.max(minimumScale, next)
      if (!Number.isFinite(nextScale)) return
      if (target) {
        const source = target.firstElementChild
        if (source) {
          const bounds = source.getBoundingClientRect()
          const view = target.getBoundingClientRect()
          const offset = point
            ? {
                x: point.x - view.left - target.clientLeft,
                y: point.y - view.top - target.clientTop,
              }
            : { x: target.clientWidth / 2, y: target.clientHeight / 2 }
          pendingAnchor.current = {
            x: (view.left + target.clientLeft + offset.x - bounds.left) / scale,
            y: (view.top + target.clientTop + offset.y - bounds.top) / scale,
            offset: {
              x: offset.x / target.clientWidth,
              y: offset.y / target.clientHeight,
            },
          }
        }
      }
      requestedScale.current = nextScale
      setZoom(next === "fit" ? "fit" : nextScale)
    },
    [fitScale, minimumScale, scale]
  )

  useEffect(() => {
    const elements = [...viewports.current.values()]
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey || !(event.currentTarget instanceof HTMLDivElement))
        return
      event.preventDefault()
      const delta =
        event.deltaY *
        (event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? event.currentTarget.clientHeight
            : 1)
      changeZoom(
        (requestedScale.current ?? scale) *
          Math.exp(-Math.max(-100, Math.min(100, delta)) * 0.01),
        event.currentTarget,
        { x: event.clientX, y: event.clientY }
      )
    }
    const start = (event: Event) => {
      event.preventDefault()
      gestureScale.current = 1
    }
    const pinch = (event: Event) => {
      if (
        !(event.currentTarget instanceof HTMLDivElement) ||
        !("scale" in event) ||
        typeof event.scale !== "number" ||
        !(event.scale > 0)
      )
        return
      event.preventDefault()
      const point =
        "clientX" in event &&
        typeof event.clientX === "number" &&
        "clientY" in event &&
        typeof event.clientY === "number"
          ? { x: event.clientX, y: event.clientY }
          : undefined
      changeZoom(
        ((requestedScale.current ?? scale) * event.scale) /
          gestureScale.current,
        event.currentTarget,
        point
      )
      gestureScale.current = event.scale
    }
    for (const element of elements) {
      element.addEventListener("wheel", wheel, { passive: false })
      element.addEventListener("gesturestart", start, { passive: false })
      element.addEventListener("gesturechange", pinch, { passive: false })
    }
    return () => {
      for (const element of elements) {
        element.removeEventListener("wheel", wheel)
        element.removeEventListener("gesturestart", start)
        element.removeEventListener("gesturechange", pinch)
      }
    }
  }, [changeZoom, scale, paneIds, firstViewport])

  function finishGesture(event: ReactPointerEvent<HTMLDivElement>) {
    if (gesture.current?.pointerId !== event.pointerId) return
    gesture.current = null
    setDragging(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId)
  }

  const comparison = (
    <div
      className={cn(
        "min-w-0 overflow-hidden border-y border-default bg-surface-level-2",
        expanded && "flex min-h-0 flex-1 flex-col"
      )}
    >
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
        <div className="flex max-w-full flex-wrap items-center gap-space-2">
          <IconButton
            size="sm"
            color="secondary"
            variant="plain"
            icon={MagnifyingGlassMinusIcon}
            label="Zoom out"
            disabled={scale <= minimumScale}
            onClick={() => changeZoom(scale / 1.25)}
          />
          <div className="w-24">
            <Slider
              aria-label="Comparison zoom"
              min={minimumScale * 100}
              max={maximumScale * 100}
              step={1}
              value={scale * 100}
              onChange={(next) =>
                changeZoom(
                  (Array.isArray(next) ? (next[0] ?? 100) : next) / 100
                )
              }
            />
          </div>
          <IconButton
            size="sm"
            color="secondary"
            variant="plain"
            icon={MagnifyingGlassPlusIcon}
            label="Zoom in"
            onClick={() => changeZoom(scale * 1.25)}
          />
          <Text
            as="span"
            variant="xs"
            className="min-w-12 text-right text-secondary tabular-nums"
            role="status"
            aria-label="Comparison zoom level"
          >
            {Math.round(scale * 100)}%
          </Text>
          <IconButton
            ref={expanded ? undefined : opener}
            size="sm"
            color="secondary"
            variant="plain"
            icon={expanded ? XIcon : ArrowsOutIcon}
            label={expanded ? "Close expanded comparison" : "Expand comparison"}
            onClick={() => setExpanded(!expanded)}
          />
        </div>
        <Text as="span" variant="xs" className="text-secondary">
          Pinch to zoom{canPan ? "; drag to pan" : ""}
        </Text>
      </div>
      <div
        className={cn(
          "grid min-w-0 divide-y divide-default sm:divide-y-0",
          expanded && "min-h-0 flex-1 auto-rows-fr",
          panes.length > 1 && "sm:grid-cols-2 sm:divide-x"
        )}
      >
        {panes.map((pane, index) => (
          <div
            key={pane.id}
            className={cn("min-w-0", expanded && "flex min-h-0 flex-col")}
          >
            <div className="border-b border-default px-space-3 py-space-2 text-xs font-medium text-secondary">
              {pane.label}
            </div>
            <div
              ref={
                index === 0
                  ? registerViewport
                  : (element) => {
                      if (element) viewports.current.set(pane.id, element)
                      else viewports.current.delete(pane.id)
                    }
              }
              role="region"
              aria-label={`${pane.label} screenshot viewport`}
              aria-description={`${canPan ? "Drag to pan or use the arrow keys to scroll. " : ""}Pinch or use plus and minus to zoom. Zero fits the screenshot.`}
              tabIndex={0}
              className={cn(
                expanded && "min-h-0 flex-1",
                "bg-surface-level-3 outline-offset-2 focus-visible:outline-2 focus-visible:outline-[var(--border-focus)]",
                canPan
                  ? cn(
                      "overflow-auto",
                      dragging ? "cursor-grabbing" : "cursor-grab"
                    )
                  : "overflow-hidden"
              )}
              style={{
                height: expanded
                  ? undefined
                  : `clamp(180px, ${((viewport.paneWidth || width) * height) / width}px, min(60vh, 560px))`,
                touchAction: canPan ? "none" : "auto",
              }}
              onScroll={(event) => synchronize(event.currentTarget)}
              onKeyDown={(event) => {
                if (
                  event.defaultPrevented ||
                  event.target !== event.currentTarget ||
                  event.ctrlKey ||
                  event.metaKey ||
                  event.altKey
                )
                  return
                if (event.key === "Escape") {
                  const current = gesture.current
                  if (current?.element.hasPointerCapture(current.pointerId))
                    current.element.releasePointerCapture(current.pointerId)
                  gesture.current = null
                  setDragging(false)
                } else if (event.key === "0") {
                  event.preventDefault()
                  changeZoom("fit")
                } else if (event.key === "+" || event.key === "=") {
                  event.preventDefault()
                  changeZoom(scale * 1.25)
                } else if (event.key === "-") {
                  event.preventDefault()
                  changeZoom(scale / 1.25)
                }
              }}
              onClickCapture={(event) => {
                if (!dragged.current) return
                event.preventDefault()
                event.stopPropagation()
                dragged.current = false
              }}
              onPointerDown={(event) => {
                dragged.current = false
                if (!canPan || event.button !== 0 || gesture.current) return
                if (
                  event.target instanceof Element &&
                  event.target.closest(
                    "button, a, input, select, textarea, [role=slider]"
                  ) &&
                  !event.target.closest("[data-screenshot]")
                )
                  return
                gesture.current = {
                  pointerId: event.pointerId,
                  element: event.currentTarget,
                  client: { x: event.clientX, y: event.clientY },
                  scroll: {
                    x: event.currentTarget.scrollLeft,
                    y: event.currentTarget.scrollTop,
                  },
                }
              }}
              onPointerMove={(event) => {
                const current = gesture.current
                if (!current || current.pointerId !== event.pointerId) return
                if (!dragged.current) {
                  if (
                    Math.hypot(
                      current.client.x - event.clientX,
                      current.client.y - event.clientY
                    ) <= 4
                  )
                    return
                  dragged.current = true
                  event.currentTarget.focus({ preventScroll: true })
                  event.currentTarget.setPointerCapture(event.pointerId)
                  setDragging(true)
                }
                event.preventDefault()
                event.currentTarget.scrollLeft =
                  current.scroll.x + current.client.x - event.clientX
                event.currentTarget.scrollTop =
                  current.scroll.y + current.client.y - event.clientY
                synchronize(event.currentTarget)
              }}
              onPointerUp={finishGesture}
              onPointerCancel={finishGesture}
              onPointerLeave={(event) => {
                if (!event.currentTarget.hasPointerCapture(event.pointerId))
                  finishGesture(event)
              }}
              onLostPointerCapture={() => {
                gesture.current = null
                setDragging(false)
              }}
            >
              <div
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

  return (
    <>
      {!expanded && comparison}
      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent
          showClose={false}
          className="h-[calc(100dvh-32px)] max-h-none w-[calc(100vw-32px)]"
          childrenClassName="min-h-0 flex-1 gap-space-3 overflow-hidden p-space-3"
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            opener.current?.focus()
          }}
        >
          <DialogTitle className="sr-only">Screenshot comparison</DialogTitle>
          <DialogDescription className="sr-only">
            Pinch or use the zoom controls to enlarge both screenshots. Drag or
            use arrow keys to pan. Press Escape to close.
          </DialogDescription>
          {expanded && comparison}
        </DialogContent>
      </Dialog>
    </>
  )
}
