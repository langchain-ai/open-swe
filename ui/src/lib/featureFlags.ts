import { useSyncExternalStore } from "react"

import type { SessionUser } from "@/lib/api"

const STORAGE_KEY = "open-swe-feature-flags-panel"

function readVisible(): boolean {
  return window.localStorage.getItem(STORAGE_KEY) === "true"
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(STORAGE_KEY, onChange)
  return () => window.removeEventListener(STORAGE_KEY, onChange)
}

export function canUseFeatureFlags(user: SessionUser | null | undefined) {
  return user?.email?.toLowerCase().endsWith("@langchain.dev") ?? false
}

/** Flips the Feature Flags tab's visibility and returns the new value. */
export function toggleFeatureFlagsPanel(): boolean {
  const next = !readVisible()
  window.localStorage.setItem(STORAGE_KEY, String(next))
  window.dispatchEvent(new Event(STORAGE_KEY))
  return next
}

export function useFeatureFlagsPanel(user: SessionUser): boolean {
  const visible = useSyncExternalStore(subscribe, readVisible, () => false)
  return canUseFeatureFlags(user) && visible
}
