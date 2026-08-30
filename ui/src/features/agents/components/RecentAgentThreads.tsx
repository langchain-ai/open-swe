import { useEffect, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"

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
  autoFocusComposer = false,
}: {
  activeThreadId: string
  autoFocusComposer?: boolean
}) {
  const inheritedThreadId = useAgentThreadStream().threadId
  const [recentThreadIds, setRecentThreadIds] = useState(() => [activeThreadId])
  const visibleThreadIds = addRecentThread(recentThreadIds, activeThreadId)

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setRecentThreadIds((current) => addRecentThread(current, activeThreadId))
  }, [activeThreadId])

  return visibleThreadIds.map((threadId) => {
    const active = threadId === activeThreadId
    const page = (
      <AgentThreadPage
        threadId={threadId}
        active={active}
        autoFocusComposer={active && autoFocusComposer}
      />
    )
    return (
      <div
        key={threadId}
        className={cn(active ? "contents" : "hidden")}
        aria-hidden={!active}
      >
        {threadId === inheritedThreadId ? (
          page
        ) : (
          <AgentThreadStreamProvider threadId={threadId}>
            {page}
          </AgentThreadStreamProvider>
        )}
      </div>
    )
  })
}
