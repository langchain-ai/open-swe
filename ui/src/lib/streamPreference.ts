import { useSyncExternalStore } from "react"

const STORAGE_KEY = "open-swe.streaming.use-stream"
const CHANGE_EVENT = "open-swe-stream-preference"

function readPreference(): boolean {
  return (
    typeof window !== "undefined" &&
    window.localStorage.getItem(STORAGE_KEY) === "true"
  )
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
  window.localStorage.setItem(STORAGE_KEY, String(enabled))
  window.dispatchEvent(new Event(CHANGE_EVENT))
}

export function useStreamPreference(): boolean {
  return useSyncExternalStore(subscribe, readPreference, () => false)
}
