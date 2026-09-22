import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate, useRouterState } from "@tanstack/react-router"

import type {
  DesktopLocalThreadSummary,
  DesktopProjectRef,
  DesktopWorkspaceMode,
} from "@/desktop"
import type { AgentThread, ImageChunk } from "@/features/agents/lib/types"
import type { CreateAgentThreadVariables } from "@/features/agents/lib/queries"
import {
  pickComposerRepo,
  pickComposerWorkspace,
  reposForWorkspace,
} from "@/features/agents/lib/composerWorkspace"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import type { RunTarget } from "@/features/agents/components/composer/RunTargetSelector"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { AgentThreadHeader } from "@/features/agents/components/AgentThreadHeader"
import { OnboardingDialog } from "@/features/agents/components/OnboardingDialog"
import { Messages } from "@/features/agents/components/messages"
import { AgentComposerDock } from "@/features/agents/components/composer/AgentComposerDock"
import { AgentRightPanel } from "@/features/agents/components/panel/AgentRightPanel"
import { LocalRepoRightPanel } from "@/features/agents/components/LocalRepoRightPanel"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
  optimisticThread,
  seedAgentThreadLists,
  useAgentSkills,
  useWorkspaceOptions,
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
import { agentsApi } from "@/features/agents/lib/api"
import { modelConfigurable } from "@/features/agents/lib/stream/promptMessage"
import { runStartCommand, startRun } from "@/features/agents/lib/transcript/api"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import { api } from "@/lib/api"
import { useProfile, useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"
import {
  requestNotificationPermission,
  setNotificationsPref,
} from "@/lib/notifications"

const LAST_LOCAL_REPO_KEY = "open-swe.desktop.last-repo"
/** Name the key had while local repositories were called projects. */
const LEGACY_LAST_LOCAL_REPO_KEY = "open-swe.desktop.last-project"
const NEW_AGENT_PANEL_ID = "new-agent"
const NEW_AGENT_PANEL_REF = {
  scope: "cloud" as const,
  threadId: NEW_AGENT_PANEL_ID,
}

/** A cloud submission whose `run.start` has not come back yet. */
interface PendingCloudSubmit {
  threadId: string
  /** Stop was pressed before the run was accepted; cancel it once it is. */
  stopRequested: boolean
}

export function AgentsHome({
  initialRepo,
  initialLocalRepo,
  initialNoRepo,
}: {
  initialRepo?: string
  initialLocalRepo?: string
  initialNoRepo?: boolean
}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const session = useSession()
  const routePending = useRouterState({
    select: (state) => state.status === "pending",
  })
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  const [autoSelected, setAutoSelected] = useState(false)
  const handleSelectionChange = (next: ModelSelection | null) => {
    setAutoSelected(next === null)
    setSelection(next)
    persistModelSelection(next, session.data?.login ?? "")
  }
  const [planMode, setPlanMode] = useState(false)
  const cloudEnabled = Boolean(session.data)
  const preferences = useQuery({
    queryKey: ["myPreferences"],
    queryFn: api.getMyPreferences,
    enabled: cloudEnabled,
  })
  // Visibility is fixed once a thread exists, so the only choice is made here,
  // seeded from the user's default and overridable per thread.
  const [visibilityOverride, setVisibilityOverride] = useState<
    "public" | "private" | null
  >(null)
  const visibility =
    visibilityOverride ?? preferences.data?.default_visibility ?? "private"
  const workspaceOptionsQuery = useWorkspaceOptions(cloudEnabled)
  const workspaces = workspaceOptionsQuery.data?.workspaces ?? []
  // undefined = untouched, so the run falls back to the repo's own workspace,
  // then the default one.
  const [workspaceOverride, setWorkspaceOverride] = useState<string | null>(
    null
  )
  const defaultWorkspaceSlug = workspaceOptionsQuery.data?.default_slug ?? null
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
  const [runTargetOverride, setRunTargetOverride] = useState<RunTarget | null>(
    initialLocalRepo ? "local" : initialRepo || initialNoRepo ? "cloud" : null
  )
  const runTarget: RunTarget = isDesktop
    ? cloudEnabled
      ? (runTargetOverride ?? desktopThreadSource)
      : "local"
    : "cloud"
  const [localRepoPath, setLocalRepoPath] = useState<string | null>(
    initialLocalRepo ?? null
  )
  const localRepoPathRef = useRef(localRepoPath)
  useEffect(() => {
    localRepoPathRef.current = localRepoPath
  }, [localRepoPath])
  const [localRepoBranch, setLocalRepoBranch] = useState<string | null>(null)
  const [localRepoBranches, setLocalRepoBranches] = useState<
    Array<DesktopProjectRef>
  >([])
  const [localWorkspaceMode, setLocalWorkspaceMode] =
    useState<DesktopWorkspaceMode>("local")
  const localWorkspaceModeRef = useRef(localWorkspaceMode)
  useEffect(() => {
    localWorkspaceModeRef.current = localWorkspaceMode
  }, [localWorkspaceMode])
  const branchRefreshId = useRef(0)
  const [localError, setLocalError] = useState<string | null>(null)
  const {
    projects: localRepos,
    loaded: localReposLoaded,
    addProject,
    removeProject,
  } = useDesktopProjects()

  const reposQuery = useRepos()
  const profileQuery = useProfile()
  const skills = useAgentSkills({ enabled: cloudEnabled })
  // undefined = untouched (the workspace decides); null = explicitly "no repo".
  const [repoOverride, setRepoOverride] = useState<string | null | undefined>(
    initialNoRepo ? null : initialRepo
  )
  const userDefaultRepo = profileQuery.data?.default_repo ?? null

  // Workspace first: an explicit pick, else the owner of a repository named
  // from outside (a link or the profile default), else the user's default,
  // then the instance default.
  const namedRepo = (
    repoOverride === undefined ? userDefaultRepo : repoOverride
  )?.toLowerCase()
  const selectedWorkspace = pickComposerWorkspace({
    override: workspaceOverride,
    repoWorkspace: namedRepo
      ? (workspaces.find((workspace) =>
          workspace.repos.some((r) => r.toLowerCase() === namedRepo)
        )?.slug ?? null)
      : null,
    userDefault: preferences.data?.default_workspace,
    instanceDefault: defaultWorkspaceSlug,
    workspaces,
  })
  // Then the repository, limited to what that workspace may work in.
  const accessibleRepos = reposQuery.data?.repositories
  // Memoized: a fresh array fed straight into the pick below reads as a
  // mutation to the React Compiler and costs the component its optimization.
  const workspaceRepos = useMemo(
    () =>
      reposForWorkspace(selectedWorkspace, workspaces, accessibleRepos ?? []),
    [accessibleRepos, selectedWorkspace, workspaces]
  )
  const repo = pickComposerRepo({
    override: repoOverride,
    userDefault: userDefaultRepo,
    workspaceDefault:
      workspaces.find((workspace) => workspace.slug === selectedWorkspace)
        ?.default_repo ?? null,
    offered: workspaceRepos,
  })
  const selectWorkspace = (slug: string | null) => {
    setWorkspaceOverride(slug)
    // A newly chosen workspace starts from its own default repository.
    setRepoOverride(undefined)
  }
  const selectRepo = (value: string | null) => {
    setRepoOverride(value)
    // Picking from the list pins the workspace it was offered under, so the
    // pick cannot drag the composer into whichever workspace owns it.
    if (!workspaceOverride) setWorkspaceOverride(selectedWorkspace)
  }

  // The picker offers the workspace being composed in its own models and
  // default, not the deployment default's.
  const { models, defaultSelection } = useModelOptions(selectedWorkspace)
  const activeSelection = autoSelected ? null : (selection ?? defaultSelection)

  // The thread id is minted here: the first `run.start` posted against it is
  // what creates the thread server-side.
  const [pendingThreadId, setPendingThreadId] = useState<string | null>(null)
  // Identity of the submission in flight, so its continuation can tell whether
  // it is still the one this page is waiting for.
  const pendingRun = useRef<PendingCloudSubmit | null>(null)
  useEffect(
    () => () => {
      pendingRun.current = null
    },
    []
  )

  useEffect(() => {
    if (pendingThreadId)
      writeStoredPanelCollapsed(pendingThreadId, panelCollapsed)
  }, [panelCollapsed, pendingThreadId])

  useEffect(() => {
    if (!isDesktop || !localReposLoaded) return
    const stored =
      window.localStorage.getItem(LAST_LOCAL_REPO_KEY) ??
      window.localStorage.getItem(LEGACY_LAST_LOCAL_REPO_KEY)
    const selected = localRepos.find(
      (checkout) => checkout.cwd === localRepoPath || checkout.cwd === stored
    )
    // oxlint-disable-next-line react/set-state-in-effect
    setLocalRepoPath(selected?.cwd ?? localRepos[0]?.cwd ?? null)
  }, [isDesktop, localRepoPath, localRepos, localReposLoaded])

  const refreshLocalRepoBranch = useCallback(async () => {
    const cwd = localRepoPathRef.current
    const refreshId = ++branchRefreshId.current
    const result = cwd
      ? await window.openSweDesktop?.getProjectBranches(cwd)
      : undefined
    if (
      localRepoPathRef.current === cwd &&
      branchRefreshId.current === refreshId
    ) {
      const branches = result?.branches ?? []
      setLocalRepoBranch((selected) =>
        localWorkspaceModeRef.current === "worktree" &&
        selected &&
        branches.some((ref) => ref.name === selected)
          ? selected
          : (result?.current ?? null)
      )
      setLocalRepoBranches(branches)
    }
  }, [])

  const selectedLocalRef = localRepoBranches.find(
    (ref) => ref.name === localRepoBranch
  )

  /**
   * A branch already checked out in a worktree can only be worked on there, so
   * selecting it runs the thread in that worktree. Otherwise "Current checkout"
   * has to switch the repository to the branch, while a worktree only starts from
   * it and is created when the thread starts.
   */
  const selectLocalRepoBranch = useCallback(
    async (branch: string) => {
      setLocalError(null)
      const ref = localRepoBranches.find(
        (candidate) => candidate.name === branch
      )
      if (ref?.worktreePath) {
        localWorkspaceModeRef.current = "worktree"
        setLocalWorkspaceMode("worktree")
        setLocalRepoBranch(branch)
        return
      }
      if (localWorkspaceMode === "worktree" || !localRepoPathRef.current) {
        setLocalRepoBranch(branch)
        return
      }
      try {
        await window.openSweDesktop?.checkoutProjectBranch({
          cwd: localRepoPathRef.current,
          branch,
        })
        setLocalRepoBranch(branch)
      } catch (error) {
        setLocalError(
          error instanceof Error ? error.message : "Could not checkout branch"
        )
      }
    },
    [localRepoBranches, localWorkspaceMode]
  )

  // A base branch chosen for a worktree was never checked out, so going back to
  // the repository's own checkout has to fall back to whatever it is really on.
  const selectLocalWorkspaceMode = useCallback(
    (next: DesktopWorkspaceMode) => {
      localWorkspaceModeRef.current = next
      setLocalWorkspaceMode(next)
      setLocalError(null)
      if (next === "local") setLocalRepoBranch(null)
      void refreshLocalRepoBranch()
    },
    [refreshLocalRepoBranch]
  )

  useEffect(() => {
    const desktop = window.openSweDesktop
    const refreshSequence = branchRefreshId
    let disposed = false
    const unsubscribe = desktop?.onProjectHeadChanged((cwd) => {
      if (cwd === localRepoPath) void refreshLocalRepoBranch()
    })
    void desktop?.watchProjectHead(localRepoPath).then(() => {
      if (!disposed) void refreshLocalRepoBranch()
    })
    void refreshLocalRepoBranch()
    return () => {
      disposed = true
      refreshSequence.current++
      unsubscribe?.()
      void desktop?.watchProjectHead(null)
    }
  }, [localRepoPath, refreshLocalRepoBranch])

  useEffect(() => {
    window.addEventListener("focus", refreshLocalRepoBranch)
    return () => window.removeEventListener("focus", refreshLocalRepoBranch)
  }, [refreshLocalRepoBranch])

  const handleRunTargetChange = (next: RunTarget) => {
    setRunTargetOverride(next)
    setDesktopThreadSource(next)
    setLocalError(null)
  }

  const handleSelectLocalRepo = (cwd: string) => {
    setLocalRepoPath(cwd)
    setRunTargetOverride("local")
    window.localStorage.setItem(LAST_LOCAL_REPO_KEY, cwd)
    setDesktopThreadSource("local")
    setLocalError(null)
  }

  const handleAddLocalRepo = async () => {
    const added = await addProject()
    if (added) handleSelectLocalRepo(added.cwd)
  }

  const handleRemoveLocalRepo = async (cwd: string) => {
    const checkout = localRepos.find((candidate) => candidate.cwd === cwd)
    if (!checkout) return
    setLocalError(null)
    try {
      const terminals = await window.openSweDesktop?.terminal.list(
        checkout.scopeId
      )
      await Promise.all(
        (terminals ?? []).map(({ terminalId }) =>
          window.openSweDesktop?.terminal.close({
            localSessionId: checkout.scopeId,
            terminalId,
            deleteHistory: true,
          })
        )
      )
      if (!(await removeProject(cwd))) return
      if (localRepoPath === cwd) setLocalRepoPath(null)
    } catch (error) {
      setLocalError(
        error instanceof Error ? error.message : "Could not remove repository"
      )
    }
  }

  const resetPendingSubmit = () => {
    pendingRun.current = null
    setPendingThreadId(null)
    setSubmittedDraft(null)
  }

  const cancelPendingThread = async (threadId: string) => {
    try {
      const cancelled = await agentsApi.cancelThread(threadId)
      queryClient.setQueryData(agentThreadKeys.detail(threadId), cancelled)
      invalidateAgentThreadLists(queryClient)
    } catch (error) {
      console.warn("Could not cancel the thread being created", error)
    }
  }

  /**
   * Stop while the thread is still being created. The start request is left to
   * finish: aborting it would not stop the server from creating the thread and
   * dispatching the run, and cancelling before the run exists either 404s or
   * finds nothing to cancel. So the run is only marked for cancellation here,
   * and the request's own continuation cancels it once it was accepted.
   */
  const stopPendingSubmit = () => {
    const pending = pendingRun.current
    if (pending) pending.stopRequested = true
    resetPendingSubmit()
  }

  const handleSubmit = (prompt: string, images: Array<ImageChunk>) => {
    void requestNotificationPermission().then((perm) => {
      if (perm === "granted") setNotificationsPref(true)
    })
    if (runTarget === "local") {
      const desktop = window.openSweDesktop
      const checkout = localRepos.find(
        (candidate) => candidate.cwd === localRepoPath
      )
      if (!desktop || !checkout) {
        setLocalError(
          "Choose or add a repository from This Mac before sending."
        )
        return
      }
      const cwd = checkout.cwd
      const draft = {
        prompt,
        images,
        model_id: activeSelection?.modelId ?? null,
        effort: activeSelection?.effort ?? null,
      }
      setSubmittedDraft(draft)
      setLocalError(null)
      window.localStorage.setItem(LAST_LOCAL_REPO_KEY, cwd)
      void (async () => {
        try {
          await refreshLocalRepoBranch()
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
            workspaceMode: localWorkspaceMode,
            baseBranch: localRepoBranch,
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
    // Minted here so the seeded thread, the graph's HumanMessage and the
    // transcript row all carry the same message id.
    const messageId = crypto.randomUUID()
    const draft = {
      prompt,
      images,
      client_message_id: messageId,
      repo,
      visibility,
      repo_explicitly_none: repoOverride === null,
      model_id: activeSelection?.modelId ?? null,
      effort: activeSelection?.effort ?? null,
    }
    setSubmittedDraft(draft)
    setLocalError(null)

    const configurable: Record<string, unknown> =
      modelConfigurable(activeSelection)
    if (repo) configurable.repo = repo
    if (repoOverride === null) configurable.repo_explicitly_none = true
    configurable.thread_type = visibility === "private" ? "private" : "workspace"
    if (planMode) configurable.plan_mode = true
    if (selectedWorkspace) configurable.workspace = selectedWorkspace

    const handleCloudSubmitError = (error: unknown) => {
      resetPendingSubmit()
      setLocalError(
        error instanceof Error
          ? error.message
          : "Could not start the cloud Open SWE agent"
      )
    }

    const threadId = crypto.randomUUID()
    const pending: PendingCloudSubmit = { threadId, stopRequested: false }
    pendingRun.current = pending
    setPendingThreadId(threadId)
    void (async () => {
      try {
        await startRun(
          threadId,
          runStartCommand({
            threadId,
            message: { id: messageId, text: prompt, images },
            configurable,
          })
        )
      } catch (error) {
        // A run that never started has nothing left to cancel, and the page
        // already went back to the empty composer when Stop was pressed.
        if (!pending.stopRequested) handleCloudSubmitError(error)
        return
      }
      // Seeded so the thread route renders the prompt immediately; the real
      // record lands with the next detail fetch.
      const thread: AgentThread = optimisticThread(threadId, draft, {
        recorded: session.data?.transcript_recording === true,
      })
      queryClient.setQueryData(agentThreadKeys.detail(threadId), thread)
      seedAgentThreadLists(queryClient, thread)
      invalidateAgentThreadLists(queryClient)
      if (pending.stopRequested) {
        await cancelPendingThread(threadId)
        return
      }
      // The user moved on (another submission, or another thread opened) while
      // this was in flight: the thread is seeded either way, but only the
      // submission this page is still waiting for may navigate.
      if (pendingRun.current !== pending) return
      await navigate({ to: "/agents/$threadId", params: { threadId } })
    })()
  }

  const handlePanelCollapsedChange = (next: boolean) => {
    setPanelCollapsed(next)
    writeStoredPanelCollapsed(NEW_AGENT_PANEL_ID, next)
  }

  const localRepo =
    runTarget === "local"
      ? localRepos.find((checkout) => checkout.cwd === localRepoPath)
      : undefined
  const optimisticDraftThread = submittedDraft
    ? optimisticThread("pending", submittedDraft)
    : null

  return (
    <>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {session.data && !routePending && <OnboardingDialog />}
        <AgentThreadHeader
          title={optimisticDraftThread?.title}
          target={runTarget === "local" ? "This Mac" : "Cloud"}
          panelCollapsed={panelCollapsed}
          visibility={runTarget === "cloud" ? visibility : undefined}
          onVisibilityChange={
            submittedDraft ? undefined : setVisibilityOverride
          }
        />
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
                src={`${import.meta.env.BASE_URL}logo-mark.png`}
                alt=""
                className="size-14 opacity-30 grayscale dark:opacity-20"
              />
              <h1 className="text-center text-2xl tracking-tight sm:text-3xl">
                What should we build?
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
                ? { threadId: pendingThreadId ?? "", running: true }
                : undefined
            }
            autoFocus
            compact
            placeholder="Do anything"
            onSubmit={handleSubmit}
            onStop={
              optimisticDraftThread && runTarget === "cloud"
                ? stopPendingSubmit
                : undefined
            }
            disabled={Boolean(submittedDraft)}
            busy={Boolean(optimisticDraftThread)}
            models={models}
            selection={activeSelection}
            onSelectionChange={handleSelectionChange}
            repos={workspaceRepos}
            selectedRepo={repo}
            onRepoChange={optimisticDraftThread ? undefined : selectRepo}
            runTarget={isDesktop ? runTarget : undefined}
            onRunTargetChange={
              !optimisticDraftThread && isDesktop && cloudEnabled
                ? handleRunTargetChange
                : undefined
            }
            localRepos={localRepos}
            selectedLocalRepoPath={localRepoPath}
            selectedLocalRepoBranch={localRepoBranch}
            localRepoBranches={localRepoBranches}
            onSelectLocalRepo={handleSelectLocalRepo}
            onAddLocalRepo={() => void handleAddLocalRepo()}
            onRemoveLocalRepo={(cwd) => void handleRemoveLocalRepo(cwd)}
            onRefreshLocalRepoBranch={() => void refreshLocalRepoBranch()}
            onSelectLocalRepoBranch={(branch) =>
              void selectLocalRepoBranch(branch)
            }
            localWorkspaceMode={localWorkspaceMode}
            localWorktreeLabel={
              selectedLocalRef?.worktreePath ? "Worktree" : undefined
            }
            onLocalWorkspaceModeChange={selectLocalWorkspaceMode}
            planMode={planMode}
            onPlanModeChange={runTarget === "cloud" ? setPlanMode : undefined}
            workspaceOptions={workspaces}
            selectedWorkspace={selectedWorkspace}
            onWorkspaceChange={
              !optimisticDraftThread && runTarget === "cloud"
                ? selectWorkspace
                : undefined
            }
            skills={skills.data}
          />
        </AgentComposerDock>
      </div>
      {localRepo ? (
        <LocalRepoRightPanel
          scopeId={localRepo.scopeId}
          cwd={localRepo.cwd}
          collapsed={panelCollapsed}
          onCollapsedChange={handlePanelCollapsedChange}
        />
      ) : (
        <AgentRightPanel
          threadRef={NEW_AGENT_PANEL_REF}
          terminals={newAgentTerminals}
          terminalTarget={{ kind: "cloud", threadId: NEW_AGENT_PANEL_ID }}
          cwd=""
          terminalAvailable={false}
          diffAvailable={false}
          collapsed={panelCollapsed}
          onCollapsedChange={handlePanelCollapsedChange}
          renderDiff={() => null}
        />
      )}
    </>
  )
}
