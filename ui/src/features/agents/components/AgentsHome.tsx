import { useCallback, useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useNavigate, useRouterState } from "@tanstack/react-router"

import type { DesktopLocalThreadSummary } from "@/desktop"
import type { ImageChunk } from "@/features/agents/lib/types"
import type { CreateAgentThreadVariables } from "@/features/agents/lib/queries"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import type { RunTarget } from "@/features/agents/components/composer/RunTargetSelector"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { AgentThreadHeader } from "@/features/agents/components/AgentThreadHeader"
import { OnboardingDialog } from "@/features/agents/components/OnboardingDialog"
import { Messages } from "@/features/agents/components/messages"
import { AgentComposerDock } from "@/features/agents/components/composer/AgentComposerDock"
import { LocalProjectSelector } from "@/features/agents/components/composer/RunTargetSelector"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import { AgentRightPanel } from "@/features/agents/components/panel/AgentRightPanel"
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
import {
  ensureDesktopModelCredential,
  localThreadKeys,
} from "@/features/agents/lib/desktopLocal"
import { useDesktopThreadSource } from "@/features/agents/lib/desktopThreadSource"
import { useAgentThreadRuntime } from "@/features/agents/lib/AgentThreadStreamProvider"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import { useProfile, useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"
import {
  requestNotificationPermission,
  setNotificationsPref,
} from "@/lib/notifications"

const LAST_LOCAL_PROJECT_KEY = "open-swe.desktop.last-project"
const NEW_AGENT_PANEL_ID = "new-agent"
const NEW_AGENT_PANEL_REF = {
  scope: "cloud" as const,
  threadId: NEW_AGENT_PANEL_ID,
}

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
  const stream = useAgentThreadRuntime()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const session = useSession()
  const routePending = useRouterState({
    select: (state) => state.status === "pending",
  })
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
  const [submittedDraft, setSubmittedDraft] =
    useState<CreateAgentThreadVariables | null>(null)
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed(NEW_AGENT_PANEL_ID)
  )
  const newAgentTerminals = useTerminalGroups(
    { kind: "cloud", threadId: NEW_AGENT_PANEL_ID },
    ""
  )
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const [desktopThreadSource, setDesktopThreadSource] = useDesktopThreadSource()
  const runTarget: RunTarget = isDesktop
    ? cloudEnabled
      ? desktopThreadSource
      : "local"
    : "cloud"
  const [localProjectPath, setLocalProjectPath] = useState<string | null>(null)
  const localProjectPathRef = useRef(localProjectPath)
  useEffect(() => {
    localProjectPathRef.current = localProjectPath
  }, [localProjectPath])
  const [localProjectBranch, setLocalProjectBranch] = useState<string | null>(
    null
  )
  const [localProjectBranches, setLocalProjectBranches] = useState<
    Array<string>
  >([])
  const [localError, setLocalError] = useState<string | null>(null)
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

  // Holds the just-submitted prompt until the SDK mints the thread id.
  const draftRef = useRef<CreateAgentThreadVariables | null>(null)

  useEffect(() => {
    const id = stream.threadId
    const draft = draftRef.current
    if (!id || !draft) return
    const thread = optimisticThread(id, draft)
    queryClient.setQueryData(agentThreadKeys.detail(id), thread)
    seedAgentThreadLists(queryClient, thread)
    invalidateAgentThreadLists(queryClient)
  }, [stream.threadId, queryClient])

  useEffect(() => {
    if (stream.threadId) {
      writeStoredPanelCollapsed(stream.threadId, panelCollapsed)
    }
  }, [panelCollapsed, stream.threadId])

  useEffect(() => {
    if (!isDesktop) return
    const stored = window.localStorage.getItem(LAST_LOCAL_PROJECT_KEY)
    const selected = localProjects.find(
      (project) => project.cwd === localProjectPath || project.cwd === stored
    )
    // oxlint-disable-next-line react/set-state-in-effect
    setLocalProjectPath(selected?.cwd ?? localProjects[0]?.cwd ?? null)
  }, [isDesktop, localProjectPath, localProjects])

  const refreshLocalProjectBranch = useCallback(async () => {
    const cwd = localProjectPathRef.current
    const result = cwd
      ? await window.openSweDesktop?.getProjectBranches(cwd)
      : undefined
    if (localProjectPathRef.current === cwd) {
      setLocalProjectBranch(result?.current ?? null)
      setLocalProjectBranches(result?.branches ?? [])
    }
  }, [])

  useEffect(() => {
    void refreshLocalProjectBranch()
  }, [localProjectPath, refreshLocalProjectBranch])

  useEffect(() => {
    window.addEventListener("focus", refreshLocalProjectBranch)
    return () => window.removeEventListener("focus", refreshLocalProjectBranch)
  }, [refreshLocalProjectBranch])

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
      await window.openSweDesktop?.checkoutProjectBranch({
        cwd: localProjectPath,
        branch,
        create,
      })
      await refreshLocalProjectBranch()
    } catch (error) {
      setLocalError(
        error instanceof Error ? error.message : "Could not checkout branch"
      )
    }
  }

  const handleAddLocalProject = async () => {
    const project = await addProject()
    if (project) handleSelectLocalProject(project.cwd)
  }

  const handleRemoveLocalProject = async (cwd: string) => {
    if (!(await removeProject(cwd))) return
    if (localProjectPath === cwd) setLocalProjectPath(null)
  }

  const resetPendingSubmit = () => {
    draftRef.current = null
    setSubmittedDraft(null)
  }

  const handleSubmit = (prompt: string, images: Array<ImageChunk>) => {
    void requestNotificationPermission().then((perm) => {
      if (perm === "granted") setNotificationsPref(true)
    })
    if (runTarget === "local") {
      const desktop = window.openSweDesktop
      const project = localProjects.find(
        (candidate) => candidate.cwd === localProjectPath
      )
      if (!desktop || !project) {
        setLocalError("Choose or add a project from This Mac before sending.")
        return
      }
      const cwd = project.cwd
      const draft = {
        prompt,
        images,
        model_id: activeSelection?.modelId ?? null,
        effort: activeSelection?.effort ?? null,
      }
      setSubmittedDraft(draft)
      setLocalError(null)
      window.localStorage.setItem(LAST_LOCAL_PROJECT_KEY, cwd)
      void (async () => {
        try {
          await refreshLocalProjectBranch()
          const credentialError = await ensureDesktopModelCredential(
            activeSelection?.modelId
          )
          if (credentialError) {
            resetPendingSubmit()
            setLocalError(credentialError)
            return
          }
          const managedSkills = cloudEnabled
            ? await skills.refetch()
            : { personal: [], organization: [] }
          const localSession = await desktop.startLocalThread({
            cwd,
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
          })
          queryClient.setQueryData(
            localThreadKeys.detail(localSession.id),
            localSession
          )
          queryClient.setQueryData<Array<DesktopLocalThreadSummary>>(
            localThreadKeys.all,
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
          resetPendingSubmit()
          setLocalError(
            error instanceof Error
              ? error.message
              : "Could not start the local Open SWE agent"
          )
        }
      })()
      return
    }
    const draft = {
      prompt,
      images,
      repo,
      repo_explicitly_none: repoOverride === null,
      model_id: activeSelection?.modelId ?? null,
      effort: activeSelection?.effort ?? null,
    }
    draftRef.current = draft
    setSubmittedDraft(draft)
    setLocalError(null)

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

    const handleCloudSubmitError = (error: unknown) => {
      resetPendingSubmit()
      setLocalError(
        error instanceof Error
          ? error.message
          : "Could not start the cloud Open SWE agent"
      )
    }
    void stream
      .submit(
        {
          messages: [{ type: "human", content: promptContent(prompt, images) }],
        },
        {
          config: { configurable },
          onError: handleCloudSubmitError,
        }
      )
      .catch(handleCloudSubmitError)
  }

  const hasProjects =
    runTarget === "local"
      ? localProjects.length > 0
      : Boolean(repo || reposQuery.data?.repositories.length)
  const optimisticDraftThread = submittedDraft
    ? optimisticThread("pending", submittedDraft)
    : null

  return (
    <>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {session.data && !routePending && <OnboardingDialog />}
        {optimisticDraftThread && (
          <AgentThreadHeader
            project={
              runTarget === "local"
                ? localProjectPath
                : optimisticDraftThread.repoFullName
            }
            target={runTarget === "local" ? "This Mac" : "Cloud"}
            panelCollapsed={panelCollapsed}
          />
        )}
        {optimisticDraftThread ? (
          <Messages
            messages={optimisticDraftThread.messages}
            isStreaming
            contentWidthClass="max-w-3xl"
          />
        ) : (
          <div className="flex min-h-0 flex-1 overflow-y-auto px-3 py-6 sm:px-6 sm:py-8">
            <div className="mx-auto flex min-h-full w-full max-w-3xl flex-1 flex-col items-center justify-center gap-6">
              <img
                src="/logo-mark.png"
                alt=""
                className="size-14 opacity-30 grayscale dark:opacity-20"
              />
              <h1 className="flex flex-wrap items-baseline justify-center gap-x-1 text-center text-2xl tracking-tight sm:text-3xl">
                {hasProjects ? (
                  <>
                    <span>What should we build in</span>
                    {runTarget === "local" ? (
                      <LocalProjectSelector
                        onAddProject={() => void handleAddLocalProject()}
                        onRemoveProject={(cwd) =>
                          void handleRemoveLocalProject(cwd)
                        }
                        onSelectProject={handleSelectLocalProject}
                        placeholder="a project"
                        projects={localProjects}
                        selectedProjectPath={localProjectPath}
                        triggerClassName="max-w-[60vw] text-2xl text-muted-foreground underline decoration-dotted underline-offset-[6px] hover:text-foreground sm:text-3xl [&>svg]:hidden"
                      />
                    ) : (
                      <RepoSelector
                        className="inline-flex"
                        emptySelectionLabel="Don't work in a project"
                        noMatchesLabel="No matching projects"
                        onRepoChange={setRepoOverride}
                        placeholder="a project"
                        repos={reposQuery.data?.repositories}
                        searchPlaceholder="Search projects…"
                        selectedLabel={repo?.split("/").at(-1)}
                        selectedRepo={repo}
                        triggerClassName="max-w-[60vw] text-2xl text-muted-foreground underline decoration-dotted underline-offset-[6px] hover:text-foreground sm:text-3xl [&>svg]:hidden"
                      />
                    )}
                    <span>?</span>
                  </>
                ) : (
                  <span>What should we build?</span>
                )}
              </h1>
            </div>
          </div>
        )}
        <AgentComposerDock>
          {localError && (
            <div className="mb-3 w-full rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {localError}
            </div>
          )}
          <AgentPromptBar
            activeRun={
              optimisticDraftThread && runTarget === "cloud"
                ? { threadId: stream.threadId ?? "", running: true }
                : undefined
            }
            autoFocus
            compact
            placeholder="Do anything"
            onSubmit={handleSubmit}
            onStop={
              optimisticDraftThread && runTarget === "cloud"
                ? () => stream.stop().finally(resetPendingSubmit)
                : undefined
            }
            disabled={Boolean(submittedDraft)}
            busy={Boolean(optimisticDraftThread)}
            models={models}
            selection={activeSelection}
            onSelectionChange={handleSelectionChange}
            repos={reposQuery.data?.repositories}
            selectedRepo={repo}
            onRepoChange={optimisticDraftThread ? undefined : setRepoOverride}
            runTarget={isDesktop ? runTarget : undefined}
            onRunTargetChange={
              !optimisticDraftThread && isDesktop && cloudEnabled
                ? handleRunTargetChange
                : undefined
            }
            localProjects={localProjects}
            selectedLocalProjectPath={localProjectPath}
            selectedLocalProjectBranch={localProjectBranch}
            localProjectBranches={localProjectBranches}
            onSelectLocalProject={handleSelectLocalProject}
            onAddLocalProject={() => void handleAddLocalProject()}
            onRemoveLocalProject={(cwd) => void handleRemoveLocalProject(cwd)}
            onRefreshLocalProjectBranch={() => void refreshLocalProjectBranch()}
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
              !optimisticDraftThread && runTarget === "cloud"
                ? setEnvironmentOverride
                : undefined
            }
            adminThread={adminThread}
            onAdminThreadChange={
              runTarget === "cloud" && session.data?.is_admin
                ? setAdminThread
                : undefined
            }
            skills={skills.data}
          />
        </AgentComposerDock>
      </div>
      <AgentRightPanel
        threadRef={NEW_AGENT_PANEL_REF}
        terminals={newAgentTerminals}
        terminalTarget={{ kind: "cloud", threadId: NEW_AGENT_PANEL_ID }}
        cwd=""
        terminalAvailable={false}
        diffAvailable={false}
        collapsed={panelCollapsed}
        onCollapsedChange={(next) => {
          setPanelCollapsed(next)
          writeStoredPanelCollapsed(NEW_AGENT_PANEL_ID, next)
        }}
        renderDiff={() => null}
      />
    </>
  )
}
