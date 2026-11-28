import { useEffect, useRef } from "react"
import type { RefObject } from "react"

import { FILE_HEADER_HEIGHT } from "./FileHeader"

/** A draggable scroll control that floats over the diff viewport. */
export function DiffScrollbar({
  viewportRef,
}: {
  viewportRef: RefObject<HTMLDivElement | null>
}) {
  const sliderRef = useRef<HTMLInputElement>(null)
  const lineHeightCache = useRef<number | undefined>(undefined)

  useEffect(() => {
    const viewport = viewportRef.current
    const slider = sliderRef.current
    if (!viewport || !slider) return

    const syncPosition = () => {
      slider.value = String(viewport.scrollTop)
    }
    const scrollViewport = (event: WheelEvent) => {
      if (
        event.defaultPrevented ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        event.deltaY === 0
      ) {
        return
      }

      let pixelsPerUnit = 1
      if (event.deltaMode === WheelEvent.DOM_DELTA_LINE) {
        lineHeightCache.current ??= Number.parseFloat(
          getComputedStyle(viewport).lineHeight
        )

        pixelsPerUnit = lineHeightCache.current || 16
      } else if (event.deltaMode === WheelEvent.DOM_DELTA_PAGE) {
        pixelsPerUnit = viewport.clientHeight
      }
      event.preventDefault()
      viewport.scrollTop += event.deltaY * pixelsPerUnit
    }

    const syncSize = () => {
      const { clientHeight, scrollHeight } = viewport
      const max = scrollHeight - clientHeight
      slider.hidden = clientHeight === 0 || max <= 0
      if (slider.hidden) return
      slider.max = String(max)
      syncPosition()
      const track = slider.clientHeight
      const thumb = Math.min(
        track,
        Math.max(24, (track * clientHeight) / scrollHeight)
      )
      slider.style.setProperty("--review-scrollbar-thumb", `${thumb}px`)
    }

    const resize = new ResizeObserver(syncSize)
    const observeContent = () => {
      resize.disconnect()
      resize.observe(viewport)
      resize.observe(slider)
      for (const child of viewport.children) resize.observe(child)
      syncSize()
    }
    const children = new MutationObserver(observeContent)
    children.observe(viewport, { childList: true })
    observeContent()

    viewport.addEventListener("scroll", syncPosition, { passive: true })
    slider.addEventListener("wheel", scrollViewport, { passive: false })
    return () => {
      resize.disconnect()
      children.disconnect()
      viewport.removeEventListener("scroll", syncPosition)
      slider.removeEventListener("wheel", scrollViewport)
    }
  }, [viewportRef])

  return (
    <div
      className="pointer-events-none absolute right-1 bottom-0 z-floating-bar"
      style={{
        top: `calc(${FILE_HEADER_HEIGHT}px + var(--review-changes-toolbar-height, 0px))`,
      }}
    >
      <input
        ref={sliderRef}
        type="range"
        aria-label="Scroll diff"
        aria-orientation="vertical"
        min={0}
        step="any"
        defaultValue={0}
        hidden
        onChange={(event) => {
          const viewport = viewportRef.current
          if (viewport) viewport.scrollTop = event.currentTarget.valueAsNumber
        }}
        className="review-diff-scrollbar pointer-events-auto h-full"
      />
    </div>
  )
}
