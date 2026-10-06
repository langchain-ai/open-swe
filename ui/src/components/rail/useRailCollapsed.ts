import { useCallback, useSyncExternalStore } from "react"

const STORAGE_KEY = "open-swe.rail.collapsed"
const NARROW_QUERY = "(max-width: 767px)"

const listeners = new Set<() => void>()

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

// A phone gets the 48px icon rail whatever the desktop preference says, so the
// work column keeps the screen; the stored choice is left alone for later.
function getSnapshot(): boolean {
  if (window.matchMedia(NARROW_QUERY).matches) return true
  return window.localStorage.getItem(STORAGE_KEY) === "1"
}

function getServerSnapshot(): boolean {
  return false
}

function setRailCollapsed(next: boolean): void {
  window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0")
  for (const listener of listeners) listener()
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
  const toggle = useCallback(() => setRailCollapsed(!collapsed), [collapsed])
  return { collapsed, setCollapsed: setRailCollapsed, toggle }
}
