import { useEffect, useRef, useState } from "react"

import { cn } from "@/lib/utils"

const HIDE_AFTER_MS = 1500
const ZOOM_EPSILON = 0.001

/**
 * Floating "X%" pill shown briefly whenever the zoom factor changes.
 * Suppressed for the mount value so a new tab never flashes 100%.
 */
export function ZoomIndicator({ zoomFactor }: { zoomFactor: number }) {
  const [visible, setVisible] = useState(false)
  const lastFactorRef = useRef(zoomFactor)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    if (Math.abs(lastFactorRef.current - zoomFactor) < ZOOM_EPSILON) return
    lastFactorRef.current = zoomFactor
    // oxlint-disable-next-line react/set-state-in-effect
    setVisible(true)
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    timerRef.current = window.setTimeout(() => {
      setVisible(false)
      timerRef.current = null
    }, HIDE_AFTER_MS)
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }
  }, [zoomFactor])

  return (
    <div
      aria-hidden={!visible}
      className={cn(
        "pointer-events-none absolute top-3 right-3 z-20 rounded-full border border-border/70 bg-popover/95 px-2.5 py-1 text-xs font-medium text-foreground shadow-md backdrop-blur transition-all duration-200 ease-out select-none",
        visible ? "translate-y-0 opacity-100" : "-translate-y-1 opacity-0"
      )}
    >
      {`${Math.round(zoomFactor * 100)}%`}
    </div>
  )
}
