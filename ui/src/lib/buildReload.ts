import { useEffect } from "react"
import type { QueryClient } from "@tanstack/react-query"

const unsentDrafts = new Set<object>()

/** Marks the caller as holding input the user has not sent, which a reload would lose. */
export function useUnsentDraft(held: boolean) {
  useEffect(() => {
    if (!held) return
    const draft = {}
    unsentDrafts.add(draft)
    return () => {
      unsentDrafts.delete(draft)
    }
  }, [held])
}

/**
 * Reloads the page to pick up a newer build, unless that would lose an unsent
 * draft or an in-flight write, or a reload for `target` already ran in this tab
 * (the server still served the old build, so another would loop).
 */
export function reloadForBuild(
  target: string,
  queryClient: QueryClient
): boolean {
  if (unsentDrafts.size > 0 || queryClient.isMutating() > 0) return false
  try {
    const key = `open-swe:reloaded-for-build:${target}`
    if (window.sessionStorage.getItem(key)) return false
    window.sessionStorage.setItem(key, "1")
  } catch (error) {
    console.warn("Could not record a build reload", error)
    return false
  }
  window.location.reload()
  return true
}
