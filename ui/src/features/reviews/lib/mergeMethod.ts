import type { MergeMethod } from "@/lib/api"

const STORAGE_KEY = "open-swe.reviews.mergeMethod"

export const mergeMethods: readonly MergeMethod[] = [
  "squash",
  "merge",
  "rebase",
]

export const mergeMethodLabels: Record<MergeMethod, string> = {
  squash: "Squash merge",
  merge: "Merge commit",
  rebase: "Rebase merge",
}

/** What the merge button says once a method is picked, in GitHub's words. */
export const mergeMethodButtonLabels: Record<MergeMethod, string> = {
  squash: "Squash and merge",
  merge: "Merge",
  rebase: "Rebase and merge",
}

export const mergeMethodDescriptions: Record<MergeMethod, string> = {
  squash: "Combine every commit into one on the base branch.",
  merge: "Add every commit to the base branch with a merge commit.",
  rebase: "Replay every commit onto the base branch.",
}

export function readPreferredMergeMethod(): MergeMethod | null {
  if (typeof window === "undefined") return null
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return mergeMethods.find((method) => method === stored) ?? null
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
