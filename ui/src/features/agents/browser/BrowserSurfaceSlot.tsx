import { useLayoutEffect, useRef } from "react"

import { acquireBrowserSurface } from "@/features/agents/browser/browserSurfaceStore"

/**
 * Placeholder the desktop webview paints over. It measures itself on layout
 * changes and publishes the rectangle to the surface store.
 */
export function BrowserSurfaceSlot(props: {
  readonly tabId: string
  readonly visible: boolean
  readonly className?: string
}) {
  const { tabId, visible, className } = props
  const elementRef = useRef<HTMLDivElement | null>(null)
  const visibleRef = useRef(visible)
  const updateRef = useRef<(() => void) | null>(null)

  useLayoutEffect(() => {
    const element = elementRef.current
    if (!element) return
    let lease = acquireBrowserSurface(tabId)
    const measure = () => {
      const rect = element.getBoundingClientRect()
      return {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.max(1, Math.round(rect.width)),
        height: Math.max(1, Math.round(rect.height)),
      }
    }
    const update = () => {
      const rect = measure()
      const shown = visibleRef.current && rect.width > 1 && rect.height > 1
      if (!lease.present(rect, shown) && visibleRef.current) {
        // A newer slot took the surface and then unmounted; reclaim it.
        lease.release()
        lease = acquireBrowserSurface(tabId)
        lease.present(rect, shown)
      }
    }
    updateRef.current = update
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    const panel = element.closest("[data-right-panel-surface-content]")
    if (panel) observer.observe(panel)
    window.addEventListener("resize", update)
    window.addEventListener("scroll", update, true)
    return () => {
      observer.disconnect()
      window.removeEventListener("resize", update)
      window.removeEventListener("scroll", update, true)
      if (updateRef.current === update) updateRef.current = null
      lease.release()
    }
  }, [tabId])

  useLayoutEffect(() => {
    visibleRef.current = visible
    updateRef.current?.()
  }, [visible])

  return (
    <div
      ref={elementRef}
      className={className}
      data-browser-surface-slot={tabId}
    />
  )
}
