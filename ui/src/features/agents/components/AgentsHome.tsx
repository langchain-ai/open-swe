import { useEffect, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { isLocalRuntime } from "@/lib/desktop-local-mode"

import type { DesktopLocalThreadSummary } from "@/desktop"
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
  useEnvironmentOptions,
} from "@/features/agents/lib/queries"
import {
  persistModelSelection,
  useModelOptions,
} from "@/features/agents/lib/provider/useModelOptions"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"
import { ensureDesktopModelCredential } from "@/features/agents/lib/desktopLocal"
import {
  localBranchesQuery,
  localKeys,
} from "@/features/agents/lib/localQueries"
import { ProjectPicker } from "@/features/agents/components/ProjectPicker"
import {
  checkoutLocalBranch,
  startLocalThread,
} from "@/features/agents/lib/localFunctions"
import { useDesktopThreadSource } from "@/features/agents/lib/desktopThreadSource"
import { useProfile, useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"
import {
  requestNotificationPermission,
  setNotificationsPref,
} from "@/lib/notifications"

const LAST_LOCAL_PROJECT_KEY = "open-swe.desktop.last-project"
const NO_BRANCHES: Array<string> = []

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
  const session = useSession()
  const { models, defaultSelection } = useModelOptions()
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  const activeSelection = selection ?? defaultSelection
  const handleSelectionChange = (next: ModelSelection) => {
    setSelection(next)
    persistModelSelection(next, session.data?.login ?? "")
  }
  const [planMode, setPlanMode] = useState(false)
  const [adminThread, setAdminThread] = useState(false)
  const cloudEnabled = Boolean(session.data)
  const environmentOptions = useEnvironmentOptions(cloudEnabled)
  const environments = environmentOptions.data?.environments ?? []
  // undefined = untouched, so the run falls back to the default environment.
  const [environmentOverride, setEnvironmentOverride] = useState<string | null>(
    null
  )
  const defaultEnvironmentSlug = environmentOptions.data?.default_slug ?? null
  const selectedEnvironment =
    environmentOverride ??
    (environments.some((env) => env.slug === defaultEnvironmentSlug)
      ? defaultEnvironmentSlug
      : null)
  const [submitting, setSubmitting] = useState(false)
  const isDesktop = isLocalRuntime()
  const [desktopThreadSource, setDesktopThreadSource] = useDesktopThreadSource()
  const runTarget: RunTarget = isDesktop
    ? cloudEnabled
      ? desktopThreadSource
      : "local"
    : "cloud"
  const [localProjectPath, setLocalProjectPath] = useState<string | null>(null)
  const branches = useQuery(localBranchesQuery(localProjectPath))
  const localProjectBranch = branches.data?.current ?? null
  const localProjectBranches = branches.data?.branches ?? NO_BRANCHES
  const [localError, setLocalError] = useState<string | null>(null)
  const [projectPickerOpen, setProjectPickerOpen] = useState(false)
  const {
    projects: localProjects,
    addProject,
    removeProject,
  } = useDesktopProjects()

  const reposQuery = useRepos()
  const profileQuery = useProfile()
  const skills = useAgentSkills({ enabled: cloudEnabled })
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
    if (!isDesktop || localProjects.length === 0) return
    const stored = window.localStorage.getItem(LAST_LOCAL_PROJECT_KEY)
    const selected = localProjects.find(
      (project) => project.cwd === localProjectPath || project.cwd === stored
    )
    // oxlint-disable-next-line react/set-state-in-effect
    setLocalProjectPath(selected?.cwd ?? localProjects[0]?.cwd ?? null)
  }, [isDesktop, localProjectPath, localProjects])

  const handleRunTargetChange = (next: RunTarget) => {
    setDesktopThreadSource(next)
    setLocalError(null)
  }

  const handleSelectLocalProject = (cwd: string) => {
    setLocalProjectPath(cwd)
    window.localStorage.setItem(LAST_LOCAL_PROJECT_KEY, cwd)
    setDesktopThreadSource("local")
    setLocalError(null)
  }

  const checkoutLocalProjectBranch = async (branch: string, create = false) => {
    if (!localProjectPath) return
    setLocalError(null)
    try {
      await checkoutLocalBranch({
        data: { cwd: localProjectPath, branch, create },
      })
      await branches.refetch()
    } catch (error) {
      setLocalError(
        error instanceof Error ? error.message : "Could not checkout branch"
      )
    }
  }

  const handleAddLocalProject = () => setProjectPickerOpen(true)

  const handleChooseLocalProject = async (cwd: string) => {
    setProjectPickerOpen(false)
    try {
      const project = await addProject(cwd)
      handleSelectLocalProject(project.cwd)
    } catch (error) {
      setLocalError(
        error instanceof Error ? error.message : "Could not add the project"
      )
    }
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
      if (!localProjectPath) {
        setLocalError("Choose or add a project before sending.")
        return
      }
      setSubmitting(true)
      setLocalError(null)
      window.localStorage.setItem(LAST_LOCAL_PROJECT_KEY, localProjectPath)
      await branches.refetch()
      try {
        const credentialError = await ensureDesktopModelCredential(
          activeSelection?.modelId
        )
        if (credentialError) {
          setSubmitting(false)
          setLocalError(credentialError)
          return
        }
        const managedSkills = cloudEnabled
          ? await skills.refetch()
          : { personal: [], organization: [] }
        const localSession = await startLocalThread({
          data: {
            cwd: localProjectPath,
            prompt,
            images,
            skills: [
              ...new Map(
                [...managedSkills.personal, ...managedSkills.organization].map(
                  (skill) => [skill.name, skill]
                )
              ).values(),
            ],
            modelId: activeSelection?.modelId,
            effort: activeSelection?.effort,
          },
        })
        queryClient.setQueryData(
          localKeys.thread(localSession.id),
          localSession
        )
        queryClient.setQueryData<Array<DesktopLocalThreadSummary>>(
          localKeys.threads,
          (current = []) => [
            localSession,
            ...current.filter((thread) => thread.id !== localSession.id),
          ]
        )
        await navigate({
          to: "/agents/local/$sessionId",
          params: { sessionId: localSession.id },
        })
      } catch (error) {
        setSubmitting(false)
        setLocalError(
          error instanceof Error
            ? error.message
            : "Could not start the local Open SWE agent"
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
    if (adminThread) configurable.admin_thread = true
    if (selectedEnvironment) configurable.environment = selectedEnvironment

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
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto px-3 py-6 sm:px-6 sm:py-8">
      {session.data && <OnboardingDialog />}
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
            onRunTargetChange={
              isDesktop && cloudEnabled ? handleRunTargetChange : undefined
            }
            localProjects={localProjects}
            selectedLocalProjectPath={localProjectPath}
            selectedLocalProjectBranch={localProjectBranch}
            localProjectBranches={localProjectBranches}
            onSelectLocalProject={handleSelectLocalProject}
            onAddLocalProject={() => void handleAddLocalProject()}
            onRemoveLocalProject={(cwd) => void handleRemoveLocalProject(cwd)}
            onRefreshLocalProjectBranch={() => void branches.refetch()}
            onSelectLocalProjectBranch={(branch) =>
              void checkoutLocalProjectBranch(branch)
            }
            onCreateLocalProjectBranch={(branch) =>
              void checkoutLocalProjectBranch(branch, true)
            }
            planMode={planMode}
            onPlanModeChange={runTarget === "cloud" ? setPlanMode : undefined}
            environments={environments}
            selectedEnvironment={selectedEnvironment}
            onEnvironmentChange={
              runTarget === "cloud" ? setEnvironmentOverride : undefined
            }
            adminThread={adminThread}
            onAdminThreadChange={
              runTarget === "cloud" && session.data?.is_admin
                ? setAdminThread
                : undefined
            }
            skills={skills.data}
          />
        </div>
      </div>
      <ProjectPicker
        open={projectPickerOpen}
        onClose={() => setProjectPickerOpen(false)}
        onChoose={handleChooseLocalProject}
      />
    </div>
  )
}
