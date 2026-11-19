import { AgentChatEmptyState } from "@/features/agents/components/AgentChatEmptyState"
import { useTraceFlight } from "@/features/trace-flight/hosts/app/launcher"
import { cn } from "@/lib/utils"
import { useEffect, useMemo, useState, useSyncExternalStore } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate, useRouter, useRouterState } from "@tanstack/react-router"
import { Banner } from "@langchain/macaw-components/Banner"
import { toast } from "sonner"

import type { AgentThread } from "@/features/agents/lib/types"
import { pickComposerRepo } from "@/features/agents/lib/composerRepo"
import { composerRepoSelection } from "@/features/agents/lib/composerRepoSelection"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import type { ComposerSubmission } from "@/features/agents/components/composer/ChatComposer"
import { AgentThreadHeader } from "@/features/agents/components/AgentThreadHeader"
import { OnboardingDialog } from "@/features/agents/components/OnboardingDialog"
import { Messages } from "@/features/agents/components/messages"
import { AgentComposerDock } from "@/features/agents/components/composer/AgentComposerDock"
import { AgentRightPanel } from "@/features/agents/components/panel/AgentRightPanel"
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
import { agentsApi } from "@/features/agents/lib/api"
import {
  pendingSenderName,
  visiblePendingMessages,
} from "@/features/agents/lib/queuedMessages"
import { modelConfigurable } from "@/features/agents/lib/stream/promptMessage"
import { runStartCommand, startRun } from "@/features/agents/lib/transcript/api"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import { useProjects } from "@/features/projects/lib/projects"
import { CreateProjectDialog } from "@/features/projects/components/CreateProjectDialog"
import { api } from "@/lib/api"
import { hasLinkedGitHub } from "@/lib/linkedGitHub"
import { useProfile, useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"
import { productAnalytics } from "@/lib/analytics"
import {
  pendingThreadStarts,
  usePendingThreadStarts,
} from "@/features/agents/lib/pendingThreadStarts"
import { reportError } from "@/lib/errorReporting"
import {
  requestNotificationPermission,
  setNotificationsPref,
} from "@/lib/notifications"

const NEW_AGENT_PANEL_ID = "new-agent"
const NEW_AGENT_PANEL_REF = {
  scope: "cloud" as const,
  threadId: NEW_AGENT_PANEL_ID,
}

export function AgentsHome({
  initialPrompt,
  initialProject,
  initialRepo,
  initialNoRepo,
}: {
  initialPrompt?: string
  /** A project the composer starts in, as from a sidebar folder's compose button. */
  initialProject?: string
  initialRepo?: string
  initialNoRepo?: boolean
}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const router = useRouter()
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
  const { projects, isPending: projectsPending } = useProjects({
    enabled: cloudEnabled,
  })
  const [projectId, setProjectId] = useState<string | null>(
    initialProject ?? null
  )
  // A project brings its repository; picking a repository leaves it.
  const selectedProject = projects.find((project) => project.id === projectId)
  const { entries, submissionId } = usePendingThreadStarts()
  const startingThread = entries.find(
    (entry) =>
      entry.thread.id === submissionId && entry.userId === session.data?.user_id
  )?.thread
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed(NEW_AGENT_PANEL_ID)
  )
  const newAgentTerminals = useTerminalGroups({ threadId: NEW_AGENT_PANEL_ID })
  const [startError, setStartError] = useState<string | null>(null)

  const reposQuery = useRepos()
  const profileQuery = useProfile()
  const skills = useAgentSkills({ enabled: cloudEnabled })
  // undefined uses defaults; null explicitly selects no repository.
  const [repoOverride, setRepoOverride] = useState<string | null | undefined>(
    initialNoRepo ? null : initialRepo
  )
  const userId = session.data?.user_id ?? ""
  const rememberedRepo = useSyncExternalStore(
    composerRepoSelection.subscribe,
    () => composerRepoSelection.read(userId),
    () => undefined
  )
  const restoredRepo =
    rememberedRepo === null
      ? null
      : reposQuery.data?.repositories.find(
          (candidate) =>
            candidate.full_name.toLowerCase() === rememberedRepo?.toLowerCase()
        )?.full_name
  // A project without a repository works without one, like "No project".
  const repoSelection =
    repoOverride !== undefined
      ? repoOverride
      : selectedProject
        ? selectedProject.repo_full_name
        : restoredRepo
  const userDefaultRepo = profileQuery.data?.default_repo ?? null

  const {
    models,
    defaultRepo,
    defaultSelection,
    modelSelectionEnabled,
    isLoading: modelsLoading,
    isError: modelsError,
  } = useModelOptions()
  const accessibleRepos = reposQuery.data?.repositories
  // Memoized: a fresh array fed straight into the pick below reads as a
  // mutation to the React Compiler and costs the component its optimization.
  const repositories = useMemo(() => accessibleRepos ?? [], [accessibleRepos])
  // Working in a repository needs GitHub; until it is linked the picker asks
  // for it, and runs go to a task folder whatever was remembered or linked.
  const gitHubLinkUser =
    session.data && !hasLinkedGitHub(session.data) ? session.data : undefined
  const pickedRepo = pickComposerRepo({
    override: repoSelection,
    userDefault: userDefaultRepo,
    defaultRepo,
    offered: repositories,
  })
  const repo = gitHubLinkUser ? null : pickedRepo
  const selectProject = (next: string | null) => {
    setProjectId(next)
    setRepoOverride(undefined)
  }
  // The name typed in the picker, while the create dialog is open.
  const [creatingProject, setCreatingProject] = useState<string | null>(null)
  const selectRepo = (value: string | null) => {
    setRepoOverride(value)
    composerRepoSelection.write(userId, value)
  }

  const activeSelection =
    !modelSelectionEnabled || autoSelected
      ? null
      : (selection ?? defaultSelection)

  useEffect(() => () => pendingThreadStarts.detach(), [])

  useEffect(() => {
    if (submissionId) writeStoredPanelCollapsed(submissionId, panelCollapsed)
  }, [panelCollapsed, submissionId])

  const cancelPendingThread = async (threadId: string) => {
    try {
      const cancelled = await agentsApi.cancelThread(threadId)
      queryClient.setQueryData(agentThreadKeys.detail(threadId), cancelled)
      invalidateAgentThreadLists(queryClient)
      return cancelled
    } catch (error) {
      reportError({ title: "Could not stop the new chat", error })
      return null
    }
  }

  const stopPendingSubmit = () => {
    if (!submissionId) return
    if (pendingThreadStarts.get(submissionId)?.phase === "accepted")
      void cancelPendingThread(submissionId)
    else pendingThreadStarts.stop(submissionId)
    pendingThreadStarts.detach()
  }

  const handleSubmit = async ({ text: prompt, images }: ComposerSubmission) => {
    void requestNotificationPermission().then((perm) => {
      if (perm === "granted") setNotificationsPref(true)
    })
    const desktop = window.openSweDesktop
    const action = productAnalytics.beginAction(
      "start_thread",
      session.data?.user_id ?? null
    )
    // Minted here so the seeded thread, the graph's HumanMessage and the
    // transcript row all carry the same message id.
    const messageId = crypto.randomUUID()
    const draft = {
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
    setStartError(null)

    const configurable: Record<string, unknown> =
      modelConfigurable(activeSelection)
    if (repo) configurable.repo = repo
    if (!repo) configurable.repo_explicitly_none = true
    configurable.thread_type =
      visibility === "private" ? "private" : "workspace"
    if (selectedProject) configurable.project_id = selectedProject.id
    const threadId = crypto.randomUUID()
    const thread: AgentThread = {
      ...optimisticThread(threadId, draft, {
        recorded: session.data?.transcript_recording === true,
        owner: session.data,
        sentAt,
      }),
      projectId: selectedProject?.id ?? null,
    }
    pendingThreadStarts.add(thread, userId)
    void (async () => {
      try {
        // An owner's private thread may ask to act on this Mac, each action approved here.
        if (visibility === "private" && desktop?.ensureLocalAccessBridge) {
          try {
            configurable.local_access_bridge_id =
              await desktop.ensureLocalAccessBridge()
          } catch (error) {
            console.warn("Local access unavailable", error)
            toast.warning(
              "Local access is unavailable; continuing in the cloud."
            )
          }
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
        action.failed(error)
        // A run that never started has nothing left to cancel, and the page
        // already went back to the empty composer when Stop was pressed.
        const stopping = pendingThreadStarts.get(threadId)?.phase === "stopping"
        // Home shows the failure inline only while the person is still on it.
        // A navigation away moves the router's location at once but keeps Home
        // mounted, and attached, until the next route has loaded.
        const pathname = router.state.location.pathname
        const onHome = pathname.replace(/\/$/, "") === "/agents"
        if (
          !stopping &&
          onHome &&
          pendingThreadStarts.read().submissionId === threadId
        ) {
          setStartError(
            error instanceof Error
              ? error.message
              : "Could not start the Robin agent"
          )
        } else if (!stopping) {
          reportError({ title: "Could not start the Robin agent", error })
        }
        if (pathname === `/agents/${threadId}`)
          await navigate({ to: "/agents" })
        pendingThreadStarts.remove(threadId)
        return
      }
      action.accepted()
      productAnalytics.track(
        "agents_thread_created",
        { has_attachments: images.length > 0 },
        session.data?.user_id ?? null
      )
      // Seeded so the thread route renders the prompt immediately; the real
      // record lands with the next detail fetch.
      queryClient.setQueryData(agentThreadKeys.detail(threadId), thread)
      seedAgentThreadLists(queryClient, thread)
      invalidateAgentThreadLists(queryClient)
      if (pendingThreadStarts.get(threadId)?.phase === "stopping") {
        const stopped = await cancelPendingThread(threadId)
        pendingThreadStarts.accept(stopped ?? thread)
        if (stopped && router.state.location.pathname === `/agents/${threadId}`)
          void navigate({ to: "/agents" })
        return
      }
      pendingThreadStarts.accept(thread)
      if (pendingThreadStarts.read().submissionId !== threadId) return
      await navigate({ to: "/agents/$threadId", params: { threadId } })
    })()
  }

  const handlePanelCollapsedChange = (next: boolean) => {
    setPanelCollapsed(next)
    writeStoredPanelCollapsed(NEW_AGENT_PANEL_ID, next)
  }

  // The thread's page shows the prompt as its pending message on arrival, so
  // the handover moves nothing.
  const startingMessages = visiblePendingMessages(
    startingThread?.pendingMessages,
    [],
    pendingSenderName(
      [],
      session.data?.login,
      session.data?.display_name || undefined
    )
  )
  const traceFlight = useTraceFlight()

  return (
    <>
      <div
        data-chat-drop-zone
        className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
      >
        {session.data && !routePending && <OnboardingDialog />}
        <AgentThreadHeader
          startingThread={startingThread ?? undefined}
          panelCollapsed={panelCollapsed}
          visibility={preferences.isPending ? undefined : visibility}
          onVisibilityChange={
            startingThread ? undefined : setVisibilityOverride
          }
        />
        <div className="relative flex min-h-0 flex-1 flex-col">
          <div
            inert={traceFlight.playing}
            className={cn(
              "flex min-h-0 flex-1 flex-col",
              !startingThread && "justify-center overflow-y-auto py-space-6"
            )}
          >
            {startingThread ? (
              <Messages
                messages={startingMessages}
                isStreaming
                contentWidthClass="max-w-3xl"
              />
            ) : (
              <AgentChatEmptyState logo={traceFlight.logo} />
            )}
            <AgentComposerDock>
              {(startError || modelsError) && (
                <Banner intent="error" className="mb-space-3 w-full">
                  {startError ??
                    "Could not load model settings. Reload to try again."}
                </Banner>
              )}
              <AgentPromptBar
                restoreDraft={
                  initialPrompt
                    ? { key: 0, text: initialPrompt, images: [] }
                    : undefined
                }
                draftKey={
                  session.data?.user_id
                    ? `${session.data.user_id}:new`
                    : undefined
                }
                activeRun={
                  startingThread
                    ? { threadId: startingThread.id, running: true }
                    : undefined
                }
                autoFocus
                compact
                placeholder="Describe a task or ask a question…"
                onSubmit={handleSubmit}
                onStop={startingThread ? stopPendingSubmit : undefined}
                disabled={Boolean(startingThread) || modelsLoading}
                busy={Boolean(startingThread)}
                models={models}
                modelSelectionEnabled={modelSelectionEnabled}
                selection={activeSelection}
                onSelectionChange={handleSelectionChange}
                repos={repositories}
                selectedRepo={repo}
                targetPending={
                  (cloudEnabled &&
                    (projectsPending || preferences.isPending)) ||
                  profileQuery.isLoading ||
                  reposQuery.isLoading ||
                  modelsLoading
                }
                onRepoChange={startingThread ? undefined : selectRepo}
                gitHubLinkUser={gitHubLinkUser}
                projects={projects}
                selectedProjectId={selectedProject?.id ?? null}
                onProjectChange={
                  !startingThread && cloudEnabled ? selectProject : undefined
                }
                onCreateProject={setCreatingProject}
                skills={skills.data}
              />
            </AgentComposerDock>
          </div>
          {traceFlight.game}
        </div>
      </div>
      {creatingProject !== null && (
        <CreateProjectDialog
          open
          onOpenChange={(open) => {
            if (!open) setCreatingProject(null)
          }}
          initialName={creatingProject}
          onCreated={(project) => selectProject(project.id)}
        />
      )}
      <AgentRightPanel
        threadRef={NEW_AGENT_PANEL_REF}
        terminals={newAgentTerminals}
        terminalTarget={{ threadId: NEW_AGENT_PANEL_ID }}
        terminalAvailable={false}
        diffAvailable={false}
        collapsed={panelCollapsed}
        onCollapsedChange={handlePanelCollapsedChange}
        renderDiff={() => null}
        renderBrowser={() => null}
        browserAvailable={false}
      />
    </>
  )
}
