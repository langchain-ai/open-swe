import type { OpenPullRequest } from "@/lib/api"

export function UnresolvedConversations({ pr }: { pr: OpenPullRequest }) {
  if (pr.unresolvedThreads === null)
    return <span className="text-ink-subtle">Conversations unavailable</span>
  if (pr.unresolvedThreads === 0) return null
  return (
    <span className="text-ink">
      {pr.unresolvedThreads} unresolved conversation
      {pr.unresolvedThreads === 1 ? "" : "s"}
    </span>
  )
}
