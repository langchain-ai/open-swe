import { useEffect, useState } from "react"

import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { AgentThreadStreamProvider } from "@/features/agents/lib/AgentThreadStreamProvider"
import { cn } from "@/lib/utils"

const MAX_MOUNTED_THREADS = 3

function addRecentThread(recentThreadIds: Array<string>, threadId: string) {
  return [threadId, ...recentThreadIds.filter((id) => id !== threadId)].slice(
    0,
    MAX_MOUNTED_THREADS
  )
}

export function RecentAgentThreads({
  activeThreadId,
}: {
  activeThreadId: string
}) {
  const [recentThreadIds, setRecentThreadIds] = useState(() => [activeThreadId])
  const visibleThreadIds = addRecentThread(recentThreadIds, activeThreadId)

  useEffect(() => {
    setRecentThreadIds((current) => addRecentThread(current, activeThreadId))
  }, [activeThreadId])

  return visibleThreadIds.map((threadId) => {
    const active = threadId === activeThreadId
    return (
      <div
        key={threadId}
        className={cn(active ? "contents" : "hidden")}
        aria-hidden={!active}
      >
        <AgentThreadStreamProvider threadId={threadId}>
          <AgentThreadPage threadId={threadId} active={active} />
        </AgentThreadStreamProvider>
      </div>
    )
  })
}
