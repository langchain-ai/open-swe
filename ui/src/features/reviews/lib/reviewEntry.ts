import type { ReviewPageRef } from "@/features/agents/lib/types"

/** Long enough to cover the navigation, short enough not to leak into a later visit. */
const ENTRY_WINDOW_MS = 2000

let lastSidebarEntry: { key: string; at: number } | null = null

export function reviewPageRoute(review: ReviewPageRef) {
  return {
    to: "/agents/reviews/$owner/$repo/$number",
    params: {
      owner: review.owner,
      repo: review.repo,
      number: String(review.number),
    },
  } as const
}

function reviewKey(review: ReviewPageRef): string {
  return `${review.owner}/${review.repo}#${review.number}`.toLowerCase()
}

/** A review opened from its sidebar row keeps the sidebar open, like a thread. */
export function noteReviewOpenedFromSidebar(review: ReviewPageRef): void {
  lastSidebarEntry = { key: reviewKey(review), at: Date.now() }
}

export function reviewOpenedFromSidebar(review: ReviewPageRef): boolean {
  return (
    lastSidebarEntry?.key === reviewKey(review) &&
    Date.now() - lastSidebarEntry.at < ENTRY_WINDOW_MS
  )
}
