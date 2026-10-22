import { useEffect, useState } from "react"
import { flushSync } from "react-dom"

import { useProfile } from "@/lib/profile"

function motionEnabled(): boolean {
  return (
    document.documentElement.hasAttribute("data-motion") &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches
  )
}

/** Gate experimental CSS motion using the personal profile flag. */
export function MotionSync() {
  const enabled = useProfile().data?.experimental_ui_motion === true
  useEffect(() => {
    document.documentElement.toggleAttribute("data-motion", enabled)
  }, [enabled])
  return null
}

/** Snapshot only explicit layout toggles when motion is enabled. */
export function withViewTransition(update: () => void): void {
  if (!document.startViewTransition || !motionEnabled()) {
    update()
    return
  }
  document.startViewTransition(() => flushSync(update))
}

/** Skip entry animations on initial load and transcript remounts. */
export function useRevealOnToggle(open: boolean) {
  const [initial] = useState(open)
  const [toggled, setToggled] = useState(false)
  if (!toggled && open !== initial) setToggled(true)
  return toggled ? { "data-motion-reveal": "" } : undefined
}
