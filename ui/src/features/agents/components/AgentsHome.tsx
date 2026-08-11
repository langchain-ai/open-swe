import { useEffect, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import type { ImageChunk } from "@/features/agents/lib/types"
import type { CreateAgentThreadVariables } from "@/features/agents/lib/queries"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import type { RunTarget } from "@/features/agents/components/composer/RunTargetSelector"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { OnboardingDialog } from "@/features/agents/components/OnboardingDialog"
import { Logo } from "@/features/agents/components/chat/Logo"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
  optimisticThread,
  seedAgentThreadLists,
  useAgentSkills,
} from "@/features/agents/lib/queries"
import {
  persistModelSelection,
  useModelOptions,
} from "@/features/agents/lib/provider/useModelOptions"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"
import { useProfile, useRepos } from "@/lib/profile"
import {
  requestNotificationPermission,
  setNotificationsPref,
} from "@/lib/notifications"

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
  const handleSelectionChange = (next: ModelSelection) => {
    setSelection(next)
    persistModelSelection(next)
  }
  const activeModel = models.find(
    (model) => model.id === activeSelection?.modelId
  )
  const [planMode, setPlanMode] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const [runTarget, setRunTarget] = useState<RunTarget>("cloud")
  const [localProjectPath, setLocalProjectPath] = useState<string | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const {
    projects: localProjects,
    addProject,
    removeProject,
  } = useDesktopProjects()

  const reposQuery = useRepos()
  const profileQuery = useProfile()
  const skills = useAgentSkills()
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
    seedAgentThreadLists(queryClient, thread)
    invalidateAgentThreadLists(queryClient)
    void navigate({ to: "/agents/$threadId", params: { threadId: id } })
  }, [stream.threadId, queryClient, navigate])

  useEffect(() => {
    if (
      localProjectPath &&
      !localProjects.some((project) => project.cwd === localProjectPath)
    ) {
      setLocalProjectPath(null)
    }
  }, [localProjectPath, localProjects])

  const handleRunTargetChange = (next: RunTarget) => {
    setRunTarget(next)
    setLocalError(null)
  }

  const handleSelectLocalProject = (cwd: string) => {
    setLocalProjectPath(cwd)
    setRunTarget("local")
    setLocalError(null)
  }

  const handleAddLocalProject = async () => {
    const project = await addProject()
    if (project) handleSelectLocalProject(project.cwd)
  }

  const handleRemoveLocalProject = async (cwd: string) => {
    if (!(await removeProject(cwd))) return
    if (localProjectPath === cwd) setLocalProjectPath(null)
  }

  const handleSubmit = async (prompt: string, images: Array<ImageChunk>) => {
    void requestNotificationPermission().then((perm) => {
      if (perm === "granted") setNotificationsPref(true)
    })
    if (runTarget === "local") {
      const desktop = window.openSweDesktop
      if (!desktop || !localProjectPath) {
        setLocalError("Choose or add a project from This Mac before sending.")
        return
      }
      setSubmitting(true)
      setLocalError(null)
      try {
        const session = await desktop.startAcpSession({
          cwd: localProjectPath,
          prompt,
          images,
          modelId: activeSelection?.modelId,
          effort: activeSelection?.effort,
        })
        await navigate({
          to: "/agents/local/$sessionId",
          params: { sessionId: session.id },
        })
      } catch (error) {
        setSubmitting(false)
        setLocalError(
          error instanceof Error
            ? error.message
            : "Could not start Deep Agents Code"
        )
        throw error
      }
      return
    }
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
    if (planMode) configurable.plan_mode = true

    await stream
      .submit(
        {
          messages: [{ type: "human", content: promptContent(prompt, images) }],
        },
        { config: { configurable } }
      )
      .catch((error) => {
        // Submit failed before the SDK minted a thread id — re-enable the
        // prompt instead of leaving it disabled until a reload.
        draftRef.current = null
        setSubmitting(false)
        throw error
      })
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto px-6 py-8">
      <OnboardingDialog />
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col items-center justify-center">
        <div className="flex w-full flex-col items-center gap-6">
          <Logo />
          {localError && (
            <div className="w-full rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {localError}
            </div>
          )}
          <AgentPromptBar
            autoFocus
            onSubmit={handleSubmit}
            disabled={submitting}
            models={models}
            selection={activeSelection}
            onSelectionChange={handleSelectionChange}
            repos={reposQuery.data?.repositories}
            selectedRepo={repo}
            onRepoChange={setRepoOverride}
            runTarget={isDesktop ? runTarget : undefined}
            onRunTargetChange={isDesktop ? handleRunTargetChange : undefined}
            localProjects={localProjects}
            selectedLocalProjectPath={localProjectPath}
            onSelectLocalProject={handleSelectLocalProject}
            onAddLocalProject={() => void handleAddLocalProject()}
            onRemoveLocalProject={(cwd) => void handleRemoveLocalProject(cwd)}
            planMode={planMode}
            onPlanModeChange={runTarget === "cloud" ? setPlanMode : undefined}
            skills={skills.data}
            contextUsage={{
              contextWindow:
                runTarget === "cloud"
                  ? (activeModel?.context_window ?? null)
                  : null,
            }}
          />
        </div>
      </div>
    </div>
  )
}
