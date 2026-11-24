import { useSyncExternalStore } from "react"

export const STREAM_PREFERENCE_STORAGE_KEY = "open-swe.streaming.use-stream.v2"
export const STREAM_PREFERENCE_DEFAULT = true
const CHANGE_EVENT = "open-swe-stream-preference"

export function readPreference(): boolean {
  if (typeof window === "undefined") return false
  const stored = window.localStorage.getItem(STREAM_PREFERENCE_STORAGE_KEY)
  return stored === null ? STREAM_PREFERENCE_DEFAULT : stored === "true"
}

function subscribe(listener: () => void): () => void {
  window.addEventListener("storage", listener)
  window.addEventListener(CHANGE_EVENT, listener)
  return () => {
    window.removeEventListener("storage", listener)
    window.removeEventListener(CHANGE_EVENT, listener)
  }
}

export function setUseStreamPreference(enabled: boolean): void {
  window.localStorage.setItem(STREAM_PREFERENCE_STORAGE_KEY, String(enabled))
  window.dispatchEvent(new Event(CHANGE_EVENT))
}

export function useStreamPreference(): boolean {
  // A server render can't read the setting and renders it off, whatever the
  // default. Rendered on, a page with the setting off would start the
  // stream's reads while it hydrates, before the setting is read.
  return useSyncExternalStore(subscribe, readPreference, () => false)
}
