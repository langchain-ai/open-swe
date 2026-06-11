import { useEffect, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import type { AgentThread, ImageChunk } from "@/lib/agents/types"
import type { CreateAgentThreadVariables } from "@/lib/agents/queries"
import type { ModelSelection } from "@/lib/agents/provider/useModelOptions"
import { AgentPromptBar } from "@/components/agents/AgentPromptBar"
import { SlackConnectDialog } from "@/components/agents/SlackConnectDialog"
import { Logo } from "@/components/agents/ported/Logo"
import { agentThreadKeys, optimisticThread } from "@/lib/agents/queries"
import { useModelOptions } from "@/lib/agents/provider/useModelOptions"
import { useProfile, useRepos } from "@/lib/profile"

function promptContent(text: string, images: Array<ImageChunk>) {
  const trimmed = text.trim()
  const imageBlocks = images.map((image) => ({
    type: "image",
    base64: image.base64,
    mime_type: image.mimeType,
    ...(image.fileName ? { file_name: image.fileName } : {}),
  }))
  return [...imageBlocks, ...(trimmed ? [{ type: "text", text: trimmed }] : [])]
}

export function AgentsHome() {
  // Submit straight through the layout's persistent stream. The SDK mints the
  // thread id (no client-minted id, no `getState` 404), fires the first
  // `run.start` — which lazily creates + stamps + owns the thread server-side
  // — and keeps streaming after we navigate to the minted thread below.
  const stream = useAgentThreadStream()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { models, defaultSelection } = useModelOptions()
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  const activeSelection = selection ?? defaultSelection
  const [submitting, setSubmitting] = useState(false)

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

  // Holds the just-submitted prompt until the SDK mints the thread id; the
  // effect then seeds the optimistic summary and navigates exactly once.
  const draftRef = useRef<CreateAgentThreadVariables | null>(null)

  useEffect(() => {
    const id = stream.threadId
    const draft = draftRef.current
    if (!id || !draft) return
    draftRef.current = null
    const thread = optimisticThread(id, draft)
    queryClient.setQueryData(agentThreadKeys.detail(id), thread)
    // Surface the thread in the sidebar immediately; the list's running
    // refetch reconciles to server truth once the run.start stamps it.
    queryClient.setQueryData<Array<AgentThread>>(agentThreadKeys.all, (prev) => [
      thread,
      ...(prev?.filter((existing) => existing.id !== id) ?? []),
    ])
    void navigate({ to: "/agents/$threadId", params: { threadId: id } })
  }, [stream.threadId, queryClient, navigate])

  const handleSubmit = (prompt: string, images: Array<ImageChunk>) => {
    draftRef.current = {
      prompt,
      images,
      repo,
      repo_explicitly_none: repoOverride === null,
      model_id: activeSelection?.modelId ?? null,
      effort: activeSelection?.effort ?? null,
    }
    setSubmitting(true)

    const configurable: Record<string, unknown> = {}
    if (activeSelection?.modelId && activeSelection.effort) {
      configurable.agent_model_id = activeSelection.modelId
      configurable.agent_effort = activeSelection.effort
    }
    if (repo) configurable.repo = repo
    if (repoOverride === null) configurable.repo_explicitly_none = true

    void stream.submit(
      { messages: [{ type: "human", content: promptContent(prompt, images) }] },
      { config: { configurable } }
    )
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto px-6 py-8">
      <SlackConnectDialog />
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col items-center justify-center">
        <div className="flex w-full flex-col items-center gap-6">
          <Logo />
          <AgentPromptBar
            onSubmit={handleSubmit}
            disabled={submitting}
            models={models}
            selection={activeSelection}
            onSelectionChange={setSelection}
            repos={reposQuery.data?.repositories}
            selectedRepo={repo}
            onRepoChange={setRepoOverride}
          />
        </div>
      </div>
    </div>
  )
}
