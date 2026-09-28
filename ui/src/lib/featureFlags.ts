import { useSyncExternalStore } from "react"

const STORAGE_KEY = "open-swe-feature-flags-panel"

function readVisible(): boolean {
  return window.localStorage.getItem(STORAGE_KEY) === "true"
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(STORAGE_KEY, onChange)
  return () => window.removeEventListener(STORAGE_KEY, onChange)
}

/** Flips the Feature Flags tab's visibility and returns the new value. */
export function toggleFeatureFlagsPanel(): boolean {
  const next = !readVisible()
  window.localStorage.setItem(STORAGE_KEY, String(next))
  window.dispatchEvent(new Event(STORAGE_KEY))
  return next
}

export function useFeatureFlagsPanel(): boolean {
  return useSyncExternalStore(subscribe, readVisible, () => false)
}
