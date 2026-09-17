import type { OpenPullRequest } from "@/lib/api"

export function UnresolvedConversations({ pr }: { pr: OpenPullRequest }) {
  if (pr.unresolvedThreads === null)
    return <span className="opacity-70">Conversations unavailable</span>
  if (pr.unresolvedThreads === 0) return null
  return (
    <span>
      {pr.unresolvedThreads} unresolved conversation
      {pr.unresolvedThreads === 1 ? "" : "s"}
    </span>
  )
}
