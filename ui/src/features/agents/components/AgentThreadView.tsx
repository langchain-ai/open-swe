import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { useQueryClient } from "@tanstack/react-query"
import { CircleAlert as CircleAlertIcon, FolderOpen } from "lucide-react"

import type {
  AgentThread,
  ImageChunk,
  Message,
} from "@/features/agents/lib/types"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert"
import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { AgentGitPanel } from "@/features/agents/components/AgentGitPanel"
import { SIBLING_COLUMN_MIN_WIDTH } from "@/features/agents/components/panel/RightPanelShell"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { ThreadPullRequests } from "@/features/agents/components/ThreadPullRequests"
import { WorkflowApprovalCard } from "@/features/agents/components/WorkflowApprovalCard"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { Messages } from "@/features/agents/components/messages"
import { OptimisticThreadHydrationRecovery } from "@/features/agents/components/OptimisticThreadHydrationRecovery"
import { latestContextTokens } from "@/features/agents/lib/contextUsage"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { useSubmitAgentMessage } from "@/features/agents/lib/provider/useSubmitAgentMessage"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import {
  agentThreadKeys,
  useAgentSkills,
  useAgentThreadMessages,
  useAgentThreadPullRequestStatus,
} from "@/features/agents/lib/queries"
import { agentsApi } from "@/features/agents/lib/api"
import { visibleQueuedMessages } from "@/features/agents/lib/queuedMessages"
import { transportForThread } from "@/features/agents/lib/threadTransport"
import { rejectPlan } from "@/lib/plan"
import { useSession } from "@/lib/session"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

interface AgentThreadViewProps {
  thread: AgentThread
  autoFocusComposer?: boolean
}

/** Paths the agent has edited this thread, newest last, for `@file` mentions. */
function editedPaths(messages: Array<Message>): Array<string> {
  const paths = new Set<string>()
  for (const message of messages) {
    for (const chunk of message.chunks) {
      if (chunk.kind !== "tool-execution" || chunk.toolKind !== "edit") continue
      const path = chunk.input?.file_path ?? chunk.input?.path
      if (typeof path === "string" && path) paths.add(path)
    }
  }
  return [...paths]
}

// The stream lives at the `/agents` layout (one persistent provider that
// survives the home → thread navigation), so this view only consumes it.
export function AgentThreadView({
  thread,
  autoFocusComposer = false,
}: AgentThreadViewProps) {
  const sendMessage = useSubmitAgentMessage(thread.id)
  const queryClient = useQueryClient()
  const stream = useAgentThreadStream()
  const isMobile = useIsMobile()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const sidebarCollapsed = useSidebarCollapsed()
  const skills = useAgentSkills()
  const session = useSession()
  const canPost = !thread.adminThread || session.data?.is_admin === true
  const canExecuteHere = transportForThread(thread).canExecuteHere(thread)
  const durableMessages = useAgentThreadMessages(thread.id).data ?? []
  const [handoffPending, setHandoffPending] = useState(false)
  const [handoffError, setHandoffError] = useState<string | null>(null)
  const canHandoff = Boolean(thread.repoFullName && isDesktop)
  const handoff = useCallback(async () => {
    if (!window.openSweDesktop || !thread.repoFullName || handoffPending) return
    setHandoffPending(true)
    setHandoffError(null)
    try {
      if (thread.status === "queued" || thread.status === "running") {
        if (thread.environment === "local") {
          await window.openSweDesktop.interruptLocalThread(thread.id)
        }
        await agentsApi.cancelThread(thread.id)
      }
      const target = thread.environment === "local" ? "cloud" : "local"
      const checkpoint =
        target === "cloud"
          ? await window.openSweDesktop.prepareCloudHandoff(thread.id)
          : undefined
      const updated = await agentsApi.handoffThread(thread.id, {
        target,
        ...(target === "local"
          ? {
              device_id: window.openSweDesktop.deviceId ?? undefined,
            }
          : { git_checkpoint: checkpoint }),
      })
      if (target === "local" && updated.deviceId && updated.gitCheckpoint) {
        await window.openSweDesktop.prepareLocalHandoff({
          threadId: updated.id,
          deviceId: updated.deviceId,
          repoFullName: updated.repoFullName,
          gitCheckpoint: updated.gitCheckpoint,
          modelId: updated.model,
          effort: updated.effort,
          messages: durableMessages,
        })
      }
      queryClient.setQueryData(agentThreadKeys.detail(thread.id), updated)
    } catch (error) {
      setHandoffError(
        error instanceof Error ? error.message : "Could not hand off thread"
      )
    } finally {
      setHandoffPending(false)
    }
  }, [durableMessages, handoffPending, queryClient, thread])
  const pullRequestStatus = useAgentThreadPullRequestStatus(
    thread.id,
    (thread.pullRequests?.length ?? 0) > 0
  )
  const pullRequestHealth = pullRequestStatus.isError
    ? undefined
    : pullRequestStatus.data?.pullRequests

  const { models, defaultSelection } = useModelOptions()
  const threadSelection = useMemo<ModelSelection | null>(() => {
    if (!thread.model || !thread.effort) return null
    const supported = models.some(
      (m) => m.id === thread.model && m.efforts.includes(thread.effort ?? "")
    )
    if (!supported) return null
    return { modelId: thread.model, effort: thread.effort }
  }, [models, thread.model, thread.effort])
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  const activeSelection = selection ?? threadSelection ?? defaultSelection
  const [planMode, setPlanMode] = useState<boolean | null>(null)
  const [planFeedbackPending, setPlanFeedbackPending] =
    useState(autoFocusComposer)
  const activePlanMode = planMode ?? thread.planMode ?? false
  const activeModel = models.find(
    (model) => model.id === activeSelection?.modelId
  )
  const submitMessage = useCallback(
    async (content: string, images: Array<ImageChunk>) => {
      if (planFeedbackPending) await rejectPlan(thread.id, false)
      await sendMessage.mutateAsync({
        content,
        images,
        model_id: activeSelection?.modelId ?? null,
        effort: activeSelection?.effort ?? null,
        plan_mode: activePlanMode,
      })
      setPlanFeedbackPending(false)
    },
    [
      activePlanMode,
      activeSelection?.effort,
      activeSelection?.modelId,
      planFeedbackPending,
      sendMessage,
      thread.id,
    ]
  )
  const submittedInitialLocalPrompt = useRef<string | null>(null)
  useEffect(() => {
    if (
      thread.environment !== "local" ||
      !canExecuteHere ||
      submittedInitialLocalPrompt.current === thread.id
    ) {
      return
    }
    submittedInitialLocalPrompt.current = thread.id
    void stream.hydrationPromise
      .then(() => window.openSweDesktop?.getLocalPrompt(thread.id))
      .then(async (pending) => {
        if (!pending) return
        await sendMessage.mutateAsync({
          content: pending.prompt,
          images: pending.images,
          model_id: activeSelection?.modelId ?? null,
          effort: activeSelection?.effort ?? null,
        })
        await window.openSweDesktop?.clearLocalPrompt(thread.id)
      })
      .catch(() => {
        submittedInitialLocalPrompt.current = null
      })
  }, [
    activeSelection?.effort,
    activeSelection?.modelId,
    canExecuteHere,
    sendMessage,
    stream.hydrationPromise,
    thread.environment,
    thread.id,
  ])
  const fixPullRequest = useCallback(
    (prompt: string) => submitMessage(prompt, []),
    [submitMessage]
  )
  const usedTokens = useMemo(
    () => latestContextTokens(stream.messages),
    [stream.messages]
  )

  // Own the git panel's collapsed state so file links can reveal the panel.
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed(thread.id)
  )
  const handlePanelCollapsedChange = useCallback(
    (next: boolean) => {
      setPanelCollapsed(next)
      writeStoredPanelCollapsed(thread.id, next)
    },
    [thread.id]
  )
  const [revealFilePath, setRevealFilePath] = useState<string | null>(null)
  const [revealChangesKey, setRevealChangesKey] = useState(0)
  const handleOpenFile = useCallback(
    (filePath: string) => {
      setRevealFilePath(filePath)
      setRevealChangesKey((key) => key + 1)
      handlePanelCollapsedChange(false)
    },
    [handlePanelCollapsedChange]
  )

  const baseMessages = useMemo<Array<Message>>(() => {
    if (thread.messages.length > 0) return thread.messages
    if (durableMessages.length > 0) return durableMessages
    return streamMessagesToUi(
      stream.messages,
      stream.toolCalls,
      messageArrivalTimestamp
    )
  }, [durableMessages, stream.messages, stream.toolCalls, thread.messages])

  const isStreaming =
    thread.status === "queued" ||
    thread.status === "running" ||
    stream.isLoading ||
    thread.messages.length > 0
  const activeRun = useMemo(
    () => ({
      threadId: thread.id,
      running: thread.status === "queued" || thread.status === "running",
    }),
    [thread.id, thread.status]
  )
  const queuedMessages = useMemo(
    () => visibleQueuedMessages(thread.queuedMessages, baseMessages),
    [baseMessages, thread.queuedMessages]
  )
  const hasMessages = baseMessages.length > 0
  const hasConversation = hasMessages || queuedMessages.length > 0
  // The only file list the UI has: whatever the agent has already touched in
  // this thread. Those are also the paths a follow-up is most likely about.
  const mentionPaths = useMemo(() => editedPaths(baseMessages), [baseMessages])
  const isThinking = stream.isLoading
  const settingUpSandbox = isThinking && baseMessages.length === 0
  // The transcript hydrates from the SDK (`GET …/state` → `stream.messages`).
  // Show a loading state during that one-time fetch instead of the empty state.
  const isHydrating = stream.isThreadLoading && !hasMessages
  // A failed hydrate is indistinguishable from an empty thread in the snapshot,
  // so say so rather than claiming the thread has no messages. `stream.error`
  // also carries run failures, hence the dedicated hydration signal.
  const [hydrateRejected, setHydrateRejected] = useState(false)
  useEffect(() => {
    let active = true
    // oxlint-disable-next-line react/set-state-in-effect
    setHydrateRejected(false)
    stream.hydrationPromise.catch(() => {
      if (active) setHydrateRejected(true)
    })
    return () => {
      active = false
    }
  }, [stream.hydrationPromise])
  const hydrationFailed = !isHydrating && !hasMessages && hydrateRejected

  return (
    <div className="flex min-w-0 flex-1">
      <OptimisticThreadHydrationRecovery
        threadId={thread.id}
        enabled={thread.messages.length > 0}
      />
      <div
        className={cn(
          "flex min-w-0 flex-1 flex-col",
          thread.adminThread && "bg-destructive/4"
        )}
        style={isMobile ? undefined : { minWidth: SIBLING_COLUMN_MIN_WIDTH }}
      >
        <header
          data-desktop-drag-region=""
          className="relative z-10 h-11 shrink-0 border-b border-border/60 bg-background/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-background/60 after:to-transparent"
        >
          <div
            className={cn(
              "flex h-full w-full items-center gap-3 px-4",
              sidebarCollapsed && (isDesktop ? "pl-32" : "pl-14"),
              panelCollapsed && "pr-14"
            )}
          >
            {thread.repoFullName && (
              <span className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-muted-foreground">
                <FolderOpen className="size-3.5 shrink-0" />
                <span className="truncate" title={thread.repoFullName}>
                  {thread.repoFullName}
                </span>
              </span>
            )}
            <span className="ml-auto shrink-0 text-xs text-muted-foreground">
              {thread.environment === "local"
                ? thread.deviceName || "This device"
                : "Cloud"}
            </span>
            {canHandoff && (
              <button
                type="button"
                className="shrink-0 rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
                disabled={
                  handoffPending ||
                  (thread.environment === "cloud" &&
                    !window.openSweDesktop?.deviceId)
                }
                onClick={() => void handoff()}
              >
                {handoffPending
                  ? "Handing off…"
                  : thread.status === "queued" || thread.status === "running"
                    ? "Interrupt & hand off"
                    : `Hand off to ${thread.environment === "local" ? "cloud" : "this Mac"}`}
              </button>
            )}
          </div>
        </header>
        {handoffError && (
          <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pt-3 text-xs text-destructive">
            {handoffError}
          </div>
        )}
        {thread.status === "error" && (
          <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pt-3">
            <Alert variant="error" controlAlignment="first-line">
              <CircleAlertIcon />
              <AlertDescription>
                <span>
                  The last run hit an error before it could finish. Send another
                  message to retry.
                </span>
              </AlertDescription>
              {thread.traceUrl && (
                <AlertAction>
                  <a
                    href={thread.traceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-md px-2 py-1 text-xs font-medium text-destructive-foreground underline underline-offset-2 hover:bg-destructive/8"
                  >
                    Open trace
                  </a>
                </AlertAction>
              )}
            </Alert>
          </div>
        )}
        <WorkflowApprovalCard
          threadId={thread.id}
          pollWhileActive={isStreaming}
        />
        {hasConversation ? (
          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
            <Messages
              messages={baseMessages}
              threadId={thread.id}
              showPlanArtifact={
                thread.planStatus === "ready" || thread.planStatus === "shared"
              }
              onOpenFile={handleOpenFile}
              queuedMessages={queuedMessages}
              isStreaming={isStreaming}
              streamIsLoading={stream.isLoading}
              isThinking={isThinking}
              settingUpSandbox={settingUpSandbox}
              contentWidthClass="max-w-3xl"
            />
            <div className="shrink-0 px-4 pb-4">
              <div className="mx-auto w-full max-w-3xl min-w-0">
                <ThreadPullRequests
                  pullRequests={thread.pullRequests ?? []}
                  health={pullRequestHealth}
                  healthUnavailable={pullRequestStatus.isError}
                  onFix={fixPullRequest}
                  fixDisabled={!canPost || sendMessage.isPending}
                />
                <AgentPromptBar
                  placeholder={
                    canPost
                      ? "Add a follow up"
                      : "Only workspace admins can send messages in this thread"
                  }
                  autoFocus={autoFocusComposer}
                  compact
                  disabled={!canPost || !canExecuteHere}
                  busy={isStreaming}
                  activeRun={activeRun}
                  onSubmit={submitMessage}
                  models={models}
                  selection={activeSelection}
                  onSelectionChange={setSelection}
                  planMode={activePlanMode}
                  onPlanModeChange={setPlanMode}
                  mentionPaths={mentionPaths}
                  skills={skills.data}
                  contextUsage={{
                    usedTokens,
                    contextWindow: activeModel?.context_window ?? null,
                  }}
                />
              </div>
            </div>
          </div>
        ) : isHydrating ? (
          <div className="flex flex-1 items-center justify-center px-6">
            <img
              src="/logo-mark.png"
              alt="Loading conversation"
              className="size-12 animate-pulse"
            />
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6">
            {hydrationFailed ? (
              <Alert variant="error" className="max-w-3xl">
                <CircleAlertIcon />
                <AlertDescription>
                  <span>
                    This thread&apos;s messages could not be loaded. Reload to
                    try again.
                  </span>
                </AlertDescription>
              </Alert>
            ) : (
              <p className="text-xs text-muted-foreground/70">
                This thread has no messages yet.
              </p>
            )}
            <div className="w-full max-w-3xl">
              <ThreadPullRequests
                pullRequests={thread.pullRequests ?? []}
                health={pullRequestHealth}
                onFix={fixPullRequest}
                fixDisabled={!canPost || sendMessage.isPending}
              />
              <AgentPromptBar
                placeholder={
                  canPost
                    ? "Send the first message"
                    : "Only workspace admins can send messages in this thread"
                }
                autoFocus={autoFocusComposer}
                compact
                disabled={!canPost || !canExecuteHere}
                busy={isStreaming}
                activeRun={activeRun}
                onSubmit={(content, images) =>
                  sendMessage.mutateAsync({
                    content,
                    images,
                    model_id: activeSelection?.modelId ?? null,
                    effort: activeSelection?.effort ?? null,
                  })
                }
                models={models}
                selection={activeSelection}
                onSelectionChange={setSelection}
                skills={skills.data}
                contextUsage={{
                  usedTokens,
                  contextWindow: activeModel?.context_window ?? null,
                }}
              />
            </div>
          </div>
        )}
      </div>
      {thread.environment === "cloud" && (
        <AgentGitPanel
          thread={thread}
          revealFilePath={revealFilePath}
          revealChangesKey={revealChangesKey}
          collapsed={panelCollapsed}
          onCollapsedChange={handlePanelCollapsedChange}
        />
      )}
    </div>
  )
}
