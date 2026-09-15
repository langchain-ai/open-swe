import type { MergeMethod } from "@/lib/api"

const STORAGE_KEY = "open-swe.reviews.mergeMethod"
const METHODS: readonly MergeMethod[] = ["squash", "merge", "rebase"]

export function readPreferredMergeMethod(): MergeMethod | null {
  if (typeof window === "undefined") return null
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return METHODS.find((method) => method === stored) ?? null
  } catch {
    return null
  }
}

export function writePreferredMergeMethod(method: MergeMethod): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(STORAGE_KEY, method)
  } catch {
    // A viewer with site data blocked simply does not get the preference back.
  }
}
