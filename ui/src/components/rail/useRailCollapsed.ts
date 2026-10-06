import { useRouterState } from "@tanstack/react-router"
import { useCallback, useEffect, useSyncExternalStore } from "react"

const STORAGE_KEY = "open-swe.rail.collapsed"
const NARROW_QUERY = "(max-width: 767px)"

const listeners = new Set<() => void>()

// A phone opens the rail over the work column for one pick and closes it on
// the next navigation, so this state is never stored: the desktop preference
// in localStorage is left alone.
let narrowOpen = false

function notify(): void {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  window.addEventListener("storage", listener)
  const media = window.matchMedia(NARROW_QUERY)
  media.addEventListener("change", listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener("storage", listener)
    media.removeEventListener("change", listener)
  }
}

function isNarrow(): boolean {
  return window.matchMedia(NARROW_QUERY).matches
}

function getSnapshot(): boolean {
  if (isNarrow()) return !narrowOpen
  return window.localStorage.getItem(STORAGE_KEY) === "1"
}

function getServerSnapshot(): boolean {
  return false
}

function setRailCollapsed(next: boolean): void {
  if (isNarrow()) {
    narrowOpen = !next
  } else {
    window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0")
  }
  notify()
}

/** The product rail's collapsed state, shared by every shell and remembered across reloads. */
export function useRailCollapsed(): {
  collapsed: boolean
  setCollapsed: (next: boolean) => void
  toggle: () => void
} {
  const collapsed = useSyncExternalStore(
    subscribe,
    getSnapshot,
    getServerSnapshot
  )
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  useEffect(() => {
    if (!narrowOpen) return
    narrowOpen = false
    notify()
  }, [pathname])
  const toggle = useCallback(() => setRailCollapsed(!collapsed), [collapsed])
  return { collapsed, setCollapsed: setRailCollapsed, toggle }
}
