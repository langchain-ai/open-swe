import { useStreamContext as useAgentThreadStream } from "@langchain/react"

import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { AgentThreadStreamProvider } from "@/features/agents/lib/AgentThreadStreamProvider"

/**
 * The opened thread, in its own stream.
 *
 * Each thread gets a provider of its own rather than re-pointing a shared one:
 * the SDK's thread switch clears the transcript and cancels whatever runs the
 * user had queued on the thread being left, so a follow-up typed during a run
 * would vanish on navigation. A provider per thread never switches.
 *
 * Only the active thread is mounted. Keeping recently-viewed threads mounted
 * used to be what made switching back feel instant, at the cost of a live SSE
 * connection and a hidden DOM tree per retained thread; the cached detail and
 * the server's transcript snapshot now cover that first paint without holding
 * streams open.
 */
export function RecentAgentThreads({
  activeThreadId,
  autoFocusComposer = false,
}: {
  activeThreadId: string
  autoFocusComposer?: boolean
}) {
  const inheritedThreadId = useAgentThreadStream().threadId
  const page = (
    <AgentThreadPage
      threadId={activeThreadId}
      autoFocusComposer={autoFocusComposer}
    />
  )
  // A thread the layout's provider already owns — the one it just minted for a
  // brand new thread — is rendered against that provider, so the run it started
  // keeps streaming instead of being re-joined by a second one.
  if (activeThreadId === inheritedThreadId) return page
  return (
    <AgentThreadStreamProvider key={activeThreadId} threadId={activeThreadId}>
      {page}
    </AgentThreadStreamProvider>
  )
}
