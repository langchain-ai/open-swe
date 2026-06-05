import { useState } from "react"

import type { ModelSelection } from "@/lib/agents/useModelOptions"
import { AgentPromptBar } from "@/components/agents/AgentPromptBar"
import { SlackConnectDialog } from "@/components/agents/SlackConnectDialog"
import { ScheduledAgentsPanel } from "@/components/agents/ScheduledAgentsPanel"
import { Logo } from "@/components/agents/ported/Logo"
import { useCreateAgentThread } from "@/lib/agents/queries"
import { useModelOptions } from "@/lib/agents/useModelOptions"
import { useProfile, useRepos } from "@/lib/profile"

export function AgentsHome() {
  const createThread = useCreateAgentThread()
  const { models, defaultSelection } = useModelOptions()
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  const activeSelection = selection ?? defaultSelection

  const reposQuery = useRepos()
  const profileQuery = useProfile()
  // undefined = untouched (fall back to the profile default); null = explicitly "no repo".
  const [repoOverride, setRepoOverride] = useState<string | null | undefined>(
    undefined
  )
  const repo =
    repoOverride === undefined
      ? (profileQuery.data?.default_repo ?? null)
      : repoOverride

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
                repo,
                model_id: activeSelection?.modelId ?? null,
                effort: activeSelection?.effort ?? null,
              })
            }
            disabled={createThread.isPending}
            models={models}
            selection={activeSelection}
            onSelectionChange={setSelection}
            repos={reposQuery.data?.repositories}
            selectedRepo={repo}
            onRepoChange={setRepoOverride}
          />
          <ScheduledAgentsPanel
            repos={reposQuery.data?.repositories}
            defaultRepo={repo}
            selection={activeSelection}
          />
        </div>
      </div>
    </div>
  )
}
