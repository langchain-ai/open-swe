import { AgentChatEmptyState } from "@/features/agents/components/AgentChatEmptyState"
import { cn } from "@/lib/utils"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { Banner } from "@langchain/macaw-components/Banner"

import type { DesktopProjectRef, DesktopWorkspaceMode } from "@/desktop"
import type { AgentThread } from "@/features/agents/lib/types"
import type { CreateAgentThreadVariables } from "@/features/agents/lib/queries"
import {
  pickComposerRepo,
  pickComposerWorkspace,
} from "@/features/agents/lib/composerWorkspace"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import type { RunTarget } from "@/features/agents/components/composer/RunTargetSelector"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import type { ComposerSubmission } from "@/features/agents/components/composer/ChatComposer"
import { AgentThreadHeader } from "@/features/agents/components/AgentThreadHeader"
import { ProjectView } from "@/features/agents/components/projects/ProjectView"
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
import { localThreadKeys } from "@/features/agents/lib/desktopLocal"
import { agentsApi } from "@/features/agents/lib/api"
import { modelConfigurable } from "@/features/agents/lib/stream/promptMessage"
import { runStartCommand, startRun } from "@/features/agents/lib/transcript/api"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import { api } from "@/lib/api"
import { reportError } from "@/lib/errorReporting"
import {
  desktopDeviceId,
  useChatProjects,
  useAssignChatProject,
} from "@/features/agents/lib/chatProjects"
import { hasLinkedGitHub } from "@/lib/linkedGitHub"
import { useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"
import {
  requestNotificationPermission,
  setNotificationsPref,
} from "@/lib/notifications"

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
  projectId,
}: {
  initialRepo?: string
  initialLocalRepo?: string
  initialNoRepo?: boolean
  projectId?: string
}) {
  const projectsQuery = useChatProjects()
  const assignProject = useAssignChatProject()
  const project = projectsQuery.data?.projects.find(
    (candidate) => candidate.id === projectId
  )
  const [projectContextApplied, setProjectContextApplied] = useState(false)
  const queryClient = useQueryClient()
  const pendingRun = useRef<PendingCloudSubmit | null>(null)
  const navigate = useNavigate()
  const session = useSession()
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  const handleSelectionChange = (next: ModelSelection | null) => {
    setSelection(next)
    persistModelSelection(next, session.data?.login ?? "")
  }
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
  const workspaces = useMemo(
    () => workspaceOptionsQuery.data?.workspaces ?? [],
    [workspaceOptionsQuery.data]
  )
  // undefined = untouched, so the run falls back to the repo's own workspace,
  // then the default one.
  const [workspaceOverride, setWorkspaceOverride] = useState<string | null>(
    null
  )
  const defaultWorkspaceSlug = workspaceOptionsQuery.data?.default_slug ?? null
  const [submitted, setSubmitted] = useState<{
    draft: CreateAgentThreadVariables
    sentAt: number
  } | null>(null)
  const submittedDraft = submitted?.draft ?? null
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed(NEW_AGENT_PANEL_ID)
  )
  const newAgentTerminals = useTerminalGroups(
    { kind: "cloud", threadId: NEW_AGENT_PANEL_ID },
    ""
  )
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const [deviceId] = useState(() => (isDesktop ? desktopDeviceId() : null))
  const [runTargetOverride, setRunTargetOverride] = useState<RunTarget | null>(
    initialLocalRepo ? "local" : initialRepo || initialNoRepo ? "cloud" : null
  )
  const runTarget: RunTarget = isDesktop
    ? cloudEnabled
      ? (runTargetOverride ?? "local")
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
    useState<DesktopWorkspaceMode>("worktree")
  const localWorkspaceModeRef = useRef(localWorkspaceMode)
  useEffect(() => {
    localWorkspaceModeRef.current = localWorkspaceMode
  }, [localWorkspaceMode])
  const branchRefreshId = useRef(0)
  const [localError, setLocalError] = useState<string | null>(null)
  const [localContextPending, setLocalContextPending] = useState(false)
  const contextLocked = Boolean(submittedDraft) || localContextPending
  const {
    projects: localRepos,
    addProject,
    removeProject,
  } = useDesktopProjects()

  const reposQuery = useRepos()
  const skills = useAgentSkills({ enabled: cloudEnabled })
  const [repoOverride, setRepoOverride] = useState<string | null | undefined>(
    initialNoRepo ? null : initialRepo
  )
  const namedRepo = repoOverride?.toLowerCase()
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
  // Then the repository: every workspace can work in every accessible one.
  const accessibleRepos = reposQuery.data?.repositories
  // Memoized: a fresh array fed straight into the pick below reads as a
  // mutation to the React Compiler and costs the component its optimization.
  const workspaceRepos = useMemo(() => accessibleRepos ?? [], [accessibleRepos])
  const repo = pickComposerRepo({
    override: repoOverride ?? null,
    userDefault: null,
    workspaceDefault: null,
    offered: workspaceRepos,
  })
  const selectWorkspace = (slug: string | null) => {
    if (pendingRun.current) return
    setProjectContextApplied(true)
    setWorkspaceOverride(slug)
  }
  const selectRepo = (value: string | null) => {
    if (pendingRun.current) return
    setProjectContextApplied(true)
    setRepoOverride(value)
    // Picking from the list pins the workspace it was offered under, so the
    // pick cannot drag the composer into whichever workspace owns it.
    if (!workspaceOverride) setWorkspaceOverride(selectedWorkspace)
  }

  const { models } = useModelOptions(selectedWorkspace)
  const activeSelection = selection

  // The thread id is minted here: the first `run.start` posted against it is
  // what creates the thread server-side.
  const [pendingThreadId, setPendingThreadId] = useState<string | null>(null)
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

  if (!projectContextApplied && project && !session.isPending) {
    setProjectContextApplied(true)
    if (!initialRepo && !initialLocalRepo && !initialNoRepo) {
      if (isDesktop && project.localFolder?.deviceId === deviceId) {
        setLocalRepoPath(project.localFolder.path)
        setRunTargetOverride("local")
      } else if (
        project.repo &&
        session.data &&
        hasLinkedGitHub(session.data)
      ) {
        setRepoOverride(project.repo)
        setRunTargetOverride("cloud")
      }
    }
  }

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
    return result
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
      if (pendingRun.current) return
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
      setLocalContextPending(true)
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
      setLocalContextPending(false)
    },
    [localRepoBranches, localWorkspaceMode]
  )

  // A base branch chosen for a worktree was never checked out, so going back to
  // the repository's own checkout has to fall back to whatever it is really on.
  const selectLocalWorkspaceMode = useCallback(
    (next: DesktopWorkspaceMode) => {
      if (pendingRun.current) return
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
    if (pendingRun.current) return
    setProjectContextApplied(true)
    setRunTargetOverride(next)
    setLocalError(null)
  }

  const handleSelectLocalRepo = (cwd: string) => {
    if (pendingRun.current) return
    setProjectContextApplied(true)
    if (cwd !== localRepoPath) {
      setLocalRepoBranch(null)
      setLocalRepoBranches([])
    }
    setLocalRepoPath(cwd)
    setRunTargetOverride("local")
    setLocalWorkspaceMode("worktree")
    localWorkspaceModeRef.current = "worktree"
    setLocalError(null)
  }

  const handleAddLocalRepo = async () => {
    const added = await addProject()
    if (added) handleSelectLocalRepo(added.cwd)
  }

  const handleRemoveLocalRepo = async (cwd: string) => {
    if (pendingRun.current) return
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
    setSubmitted(null)
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

  const handleSubmit = ({ text: prompt, images }: ComposerSubmission) => {
    if (pendingRun.current || localContextPending) return
    if (projectId && (!project || projectsQuery.isPending)) return
    void requestNotificationPermission().then((perm) => {
      if (perm === "granted") setNotificationsPref(true)
    })
    const desktop = window.openSweDesktop
    const localCheckout =
      runTarget === "local" && localRepoPath
        ? { cwd: localRepoPath }
        : undefined
    const localRun = runTarget === "local"
    if (localRun && !desktop) {
      setLocalError("Choose or add a repository from This Mac before sending.")
      return
    }
    if (localRun && !localCheckout && !desktop?.taskWorkspaces) {
      setLocalError(
        "Update Open SWE Desktop to start without selecting a folder."
      )
      return
    }
    // Minted here so the seeded thread, the graph's HumanMessage and the
    // transcript row all carry the same message id.
    const messageId = crypto.randomUUID()
    // A "This Mac" thread's repo is its checkout's, named once it is prepared,
    // and its transcript is its owner's: nobody else can run it.
    const draft = localRun
      ? {
          prompt,
          images,
          client_message_id: messageId,
          visibility: "private" as const,
          model_id: activeSelection?.modelId ?? null,
          effort: activeSelection?.effort ?? null,
        }
      : {
          prompt,
          images,
          client_message_id: messageId,
          repo,
          visibility,
          repo_explicitly_none: !repo,
          model_id: activeSelection?.modelId ?? null,
          effort: activeSelection?.effort ?? null,
        }
    const sentAt = Date.now()
    setSubmitted({ draft, sentAt })
    setLocalError(null)

    const configurable: Record<string, unknown> =
      modelConfigurable(activeSelection)
    configurable.experience = "agent_workspace"
    if (localRun) {
      configurable.thread_type = "private"
    } else {
      if (repo) configurable.repo = repo
      if (!repo) configurable.repo_explicitly_none = true
      configurable.thread_type =
        visibility === "private" ? "private" : "workspace"
      if (selectedWorkspace) configurable.workspace = selectedWorkspace
    }

    const threadId = crypto.randomUUID()
    const pending: PendingCloudSubmit = { threadId, stopRequested: false }
    pendingRun.current = pending
    setPendingThreadId(threadId)
    void (async () => {
      let prepared = false
      try {
        if (localRun && desktop) {
          const refs = await refreshLocalRepoBranch()
          const local = await desktop.prepareLocalThread({
            threadId,
            cwd: localCheckout?.cwd,
            taskWorkspace: true,
            workspaceMode:
              localCheckout && refs?.branches.length
                ? localWorkspaceMode
                : "local",
            baseBranch: localRepoBranch ?? refs?.current,
          })
          prepared = true
          // Awaited so the thread route already knows it is This Mac's.
          await queryClient.invalidateQueries({ queryKey: localThreadKeys.all })
          configurable.sandbox_bridge_id = local.bridgeId
          if (local.repo && session.data && hasLinkedGitHub(session.data))
            configurable.repo = local.repo
          else configurable.repo_explicitly_none = true
        }
        await startRun(
          threadId,
          runStartCommand({
            threadId,
            message: { id: messageId, text: prompt, images },
            configurable,
          })
        )
      } catch (error) {
        // Nothing in the cloud refers to the checkout this prepared.
        if (prepared && desktop?.preservesThreadFiles)
          void desktop
            .discardLocalThread(threadId)
            .catch((cause: unknown) =>
              console.warn("Could not discard an unstarted local thread", cause)
            )
        // A run that never started has nothing left to cancel, and the page
        // already went back to the empty composer when Stop was pressed.
        if (!pending.stopRequested) {
          resetPendingSubmit()
          setLocalError(
            error instanceof Error
              ? error.message
              : "Could not start the Open SWE agent"
          )
        }
        return
      }
      // Seeded so the thread route renders the prompt immediately; the real
      // record lands with the next detail fetch.
      const thread: AgentThread = {
        experience: "agent_workspace",
        ...optimisticThread(threadId, draft, {
          recorded: session.data?.transcript_recording === true,
          owner: session.data,
          sentAt,
        }),
        // So the seeded thread already reads as This Mac, not Cloud, until
        // the server's record replaces it.
        ...(localRun ? { sandboxBridgeClient: "desktop" as const } : {}),
      }
      queryClient.setQueryData(agentThreadKeys.detail(threadId), thread)
      seedAgentThreadLists(queryClient, thread)
      invalidateAgentThreadLists(queryClient)
      if (projectId) {
        void assignProject
          .mutateAsync({ threadId, projectId })
          .catch((error: unknown) => {
            reportError({
              title: "Chat created, but couldn't add it to the project",
              error,
            })
          })
      }
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
  const optimisticDraftThread = submitted
    ? optimisticThread("pending", submitted.draft, {
        owner: session.data,
        sentAt: submitted.sentAt,
      })
    : null

  const newChatContent = (
    <>
      {optimisticDraftThread ? (
        <Messages
          messages={optimisticDraftThread.messages}
          isStreaming
          contentWidthClass="max-w-3xl"
        />
      ) : (
        <AgentChatEmptyState projectName={project?.name} />
      )}
      <AgentComposerDock>
        {projectId && !projectsQuery.isPending && !project && (
          <Banner intent="error" className="mb-space-3 w-full">
            {projectsQuery.isError
              ? "Couldn't load this project. Try again or start a new chat."
              : "This project is no longer available. Start a new chat from the sidebar."}
          </Banner>
        )}
        {project?.localFolder &&
          (!isDesktop || project.localFolder.deviceId !== deviceId) &&
          !localRepoPath && (
            <Banner intent="warning" className="mb-space-3 w-full">
              {isDesktop
                ? `This project's folder is on another Mac and won't be used here. Choose a folder in Working context, or continue with ${runTarget === "local" ? "an automatic task folder" : "Cloud"}.`
                : "This project's folder isn't available in the browser. Continue in Cloud, or choose a folder in the desktop app."}
            </Banner>
          )}
        {project?.repo && session.data && !hasLinkedGitHub(session.data) && (
          <Banner intent="warning" className="mb-space-3 w-full">
            Link GitHub in Connections to use this project's GitHub repository.
            Local folders and chat are still available.
          </Banner>
        )}
        {localError && (
          <Banner intent="error" className="mb-space-3 w-full">
            {localError}
          </Banner>
        )}
        <AgentPromptBar
          key={`${session.data?.user_id}:new`}
          draftKey={
            session.data?.user_id ? `${session.data.user_id}:new` : undefined
          }
          activeRun={
            optimisticDraftThread
              ? { threadId: pendingThreadId ?? "", running: true }
              : undefined
          }
          autoFocus
          compact
          placeholder="Ask a question or describe a task…"
          contextProjectName={project?.name}
          onSubmit={handleSubmit}
          onStop={optimisticDraftThread ? stopPendingSubmit : undefined}
          disabled={
            contextLocked ||
            Boolean(projectId && (!project || projectsQuery.isPending))
          }
          busy={Boolean(optimisticDraftThread)}
          models={models}
          selection={activeSelection}
          onSelectionChange={handleSelectionChange}
          repos={workspaceRepos}
          selectedRepo={repo}
          onRepoChange={contextLocked ? undefined : selectRepo}
          runTarget={runTarget}
          onRunTargetChange={
            !contextLocked && isDesktop && cloudEnabled
              ? handleRunTargetChange
              : undefined
          }
          localRepos={localRepos}
          selectedLocalRepoPath={localRepoPath}
          selectedLocalRepoBranch={localRepoBranch}
          localRepoBranches={localRepoBranches}
          onSelectLocalRepo={contextLocked ? undefined : handleSelectLocalRepo}
          onUseTaskFolder={
            !contextLocked
              ? () => {
                  setProjectContextApplied(true)
                  setLocalRepoPath(null)
                  setLocalRepoBranch(null)
                  setLocalRepoBranches([])
                  setLocalError(null)
                }
              : undefined
          }
          onAddLocalRepo={
            contextLocked ? undefined : () => void handleAddLocalRepo()
          }
          onRemoveLocalRepo={
            contextLocked ? undefined : (cwd) => void handleRemoveLocalRepo(cwd)
          }
          onRefreshLocalRepoBranch={
            contextLocked ? undefined : () => void refreshLocalRepoBranch()
          }
          onSelectLocalRepoBranch={
            contextLocked
              ? undefined
              : (branch) => void selectLocalRepoBranch(branch)
          }
          localWorkspaceMode={localWorkspaceMode}
          localWorktreeLabel={
            selectedLocalRef?.worktreePath ? "Worktree" : undefined
          }
          onLocalWorkspaceModeChange={
            contextLocked ? undefined : selectLocalWorkspaceMode
          }
          workspaceOptions={workspaces}
          selectedWorkspace={selectedWorkspace}
          onWorkspaceChange={
            !contextLocked && runTarget === "cloud"
              ? selectWorkspace
              : undefined
          }
          skills={skills.data}
        />
      </AgentComposerDock>
    </>
  )

  return (
    <>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <AgentThreadHeader
          title={optimisticDraftThread?.title}
          target={runTarget === "local" ? "This Mac" : "Cloud"}
          panelCollapsed={panelCollapsed}
          visibility={runTarget === "cloud" ? visibility : undefined}
          onVisibilityChange={
            submittedDraft ? undefined : setVisibilityOverride
          }
        />
        <div
          className={cn(
            "flex min-h-0 flex-1 flex-col",
            !optimisticDraftThread &&
              (project
                ? "overflow-y-auto py-space-6"
                : "justify-center overflow-y-auto py-space-6")
          )}
        >
          {project && !optimisticDraftThread ? (
            <ProjectView project={project}>{newChatContent}</ProjectView>
          ) : (
            newChatContent
          )}
        </div>
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
          renderBrowser={() => null}
          browserAvailable={false}
        />
      )}
    </>
  )
}
