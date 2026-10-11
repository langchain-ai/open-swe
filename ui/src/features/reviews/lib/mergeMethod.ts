import type { MergeMethod } from "@/lib/api"
import {
  readPreference,
  writePreference,
} from "@/features/reviews/lib/preferences"

const STORAGE_KEY = "open-swe.reviews.mergeMethod"

export const mergeMethods: readonly MergeMethod[] = [
  "squash",
  "merge",
  "rebase",
]

/** How each method is named in the menu, on the button once chosen (GitHub's words), and explained. */
export const mergeMethodCopy: Record<
  MergeMethod,
  { label: string; button: string; description: string }
> = {
  squash: {
    label: "Squash merge",
    button: "Squash and merge",
    description: "Combine every commit into one on the base branch.",
  },
  merge: {
    label: "Merge commit",
    button: "Merge",
    description: "Add every commit to the base branch with a merge commit.",
  },
  rebase: {
    label: "Rebase merge",
    button: "Rebase and merge",
    description: "Replay every commit onto the base branch.",
  },
}

export function readPreferredMergeMethod(): MergeMethod | null {
  const stored = readPreference(STORAGE_KEY)
  return mergeMethods.find((method) => method === stored) ?? null
}

export function writePreferredMergeMethod(method: MergeMethod): void {
  writePreference(STORAGE_KEY, method)
}
