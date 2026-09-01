import { useSyncExternalStore } from "react"

const STORAGE_KEY = "open-swe-show-sender-context"
const EVENT = "open-swe-sender-context-change"

export function showSenderContext(): boolean {
  return (
    typeof window !== "undefined" &&
    localStorage.getItem(STORAGE_KEY) === "true"
  )
}

export function setShowSenderContext(show: boolean): void {
  localStorage.setItem(STORAGE_KEY, String(show))
  window.dispatchEvent(new Event(EVENT))
}

export function useShowSenderContext(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      window.addEventListener(EVENT, onChange)
      return () => window.removeEventListener(EVENT, onChange)
    },
    showSenderContext,
    () => false
  )
}
