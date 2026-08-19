import { useSyncExternalStore } from "react"

export type DesktopThreadSource = "local" | "cloud"

const STORAGE_KEY = "open-swe.agents.desktop-thread-source"
const listeners = new Set<() => void>()
let storageListenerAttached = false

export function readStoredDesktopThreadSource(): DesktopThreadSource {
  if (typeof window === "undefined") return "local"
  return window.localStorage.getItem(STORAGE_KEY) === "cloud"
    ? "cloud"
    : "local"
}

function handleStorage(event: StorageEvent): void {
  if (event.key !== STORAGE_KEY) return
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (typeof window !== "undefined" && !storageListenerAttached) {
    window.addEventListener("storage", handleStorage)
    storageListenerAttached = true
  }
  return () => {
    listeners.delete(listener)
    if (
      typeof window !== "undefined" &&
      storageListenerAttached &&
      listeners.size === 0
    ) {
      window.removeEventListener("storage", handleStorage)
      storageListenerAttached = false
    }
  }
}

export function writeStoredDesktopThreadSource(
  source: DesktopThreadSource
): void {
  if (typeof window === "undefined") return
  if (readStoredDesktopThreadSource() === source) return
  window.localStorage.setItem(STORAGE_KEY, source)
  listeners.forEach((listener) => listener())
}

export function useDesktopThreadSource(): [
  DesktopThreadSource,
  (source: DesktopThreadSource) => void,
] {
  const source = useSyncExternalStore(
    subscribe,
    readStoredDesktopThreadSource,
    (): DesktopThreadSource => "local"
  )
  return [source, writeStoredDesktopThreadSource]
}
