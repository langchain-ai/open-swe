import { Link } from "@tanstack/react-router"
import { useEffect, useState } from "react"

import type { ModelSelection } from "@/lib/agents/useModelOptions"
import { AgentPromptBar } from "@/components/agents/AgentPromptBar"
import { AgentRunCard } from "@/components/agents/AgentRunCard"
import { SlackConnectDialog } from "@/components/agents/SlackConnectDialog"
import { Logo } from "@/components/agents/ported/Logo"
import { useAgentThreads, useCreateAgentThread } from "@/lib/agents/queries"
import { useModelOptions } from "@/lib/agents/useModelOptions"

export function AgentsHome() {
  const threadsQuery = useAgentThreads()
  const createThread = useCreateAgentThread()
  const recentRuns = (threadsQuery.data ?? []).slice(0, 5)
  const { models, defaultSelection } = useModelOptions()
  const [selection, setSelection] = useState<ModelSelection | null>(null)

  useEffect(() => {
    if (selection === null && defaultSelection) setSelection(defaultSelection)
  }, [defaultSelection, selection])

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto px-6 py-8">
      <SlackConnectDialog />
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col items-center justify-center">
        <div className="flex w-full flex-col items-center gap-6">
          <Logo />
          <AgentPromptBar
            onSubmit={(prompt) =>
              createThread.mutate({
                prompt,
                model_id: selection?.modelId ?? null,
                effort: selection?.effort ?? null,
              })
            }
            disabled={createThread.isPending}
            models={models}
            selection={selection ?? defaultSelection}
            onSelectionChange={setSelection}
          />
        </div>

        <div className="mx-auto mt-6 w-full max-w-[640px] space-y-2">
          {threadsQuery.isLoading ? (
            <p className="text-center text-sm text-[var(--ui-text-dim)]">
              Loading agents…
            </p>
          ) : recentRuns.length === 0 ? (
            <AgentsHomeEmptyState />
          ) : (
            recentRuns.map((thread) => (
              <AgentRunCard key={thread.id} thread={thread} />
            ))
          )}
        </div>
      </div>
    </div>
  )
}

export function AgentsHomeEmptyState() {
  return (
    <div className="flex flex-col items-center gap-3 py-4 text-center">
      <p className="text-sm text-[var(--ui-text-muted)]">No agents yet.</p>
      <Link
        to="/agents"
        className="text-sm font-medium text-[var(--ui-accent)] hover:underline"
      >
        Start your first agent
      </Link>
    </div>
  )
}
