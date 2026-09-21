import {
  Profiler,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  ArrowUpRight,
  CircleAlert as CircleAlertIcon,
  GitMerge as GitMergeIcon,
} from "lucide-react"
import { IoLogoSlack } from "react-icons/io5"
import { LoadError, useLoadTimedOut } from "@/components/LoadError"

import type {
  AgentPullRequest,
  AgentThread,
  ImageChunk,
  Message,
} from "@/features/agents/lib/types"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert"
import { AgentGitPanel } from "@/features/agents/components/AgentGitPanel"
import { AgentThreadHeader } from "@/features/agents/components/AgentThreadHeader"
import { SIBLING_COLUMN_MIN_WIDTH } from "@/features/agents/components/panel/RightPanelShell"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { AgentComposerDock } from "@/features/agents/components/composer/AgentComposerDock"
import { PullRequestPreviewProvider } from "@/features/agents/components/PullRequestPreview"
import { ThreadPullRequests } from "@/features/agents/components/ThreadPullRequests"
import { ThreadFeedbackCard } from "@/features/agents/components/ThreadFeedbackCard"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { Messages } from "@/features/agents/components/messages"
import type {
  LoadEarlier,
  MessagesScrollControl,
} from "@/features/agents/components/messages"
import { useSubmitAgentMessage } from "@/features/agents/lib/provider/useSubmitAgentMessage"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import {
  useAgentSkills,
  useRenameAgentThread,
  useAgentThreadPullRequestStatus,
} from "@/features/agents/lib/queries"
import { visiblePendingMessages } from "@/features/agents/lib/queuedMessages"
import {
  isQueuedMessageDue,
  latestCompletedToolCallId,
  useQueuedMessageStore,
  useQueuedMessages,
} from "@/features/agents/lib/queuedMessageStore"
import type {
  QueuePhase,
  QueuedComposerMessage,
} from "@/features/agents/lib/queuedMessageStore"
import type { RestoredDraft } from "@/features/agents/components/composer/ChatComposer"
import { agentsApi } from "@/features/agents/lib/api"
import { rejectPlan } from "@/lib/plan"
import { useSession } from "@/lib/session"
import { useIsMobile } from "@/lib/useIsMobile"
import { useThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import { useConnectionStatus } from "@/features/agents/lib/stream/useReconnectStatus"
import { runTranscriptCommitted } from "@/lib/perf/streaming"
import {
  threadHydrated,
  threadHydrationFailed,
  threadTranscriptPainted,
} from "@/lib/perf/threadLoad"

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

function CodeChannelLink({ url }: { url?: string | null }) {
  if (!url) return null
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="mb-2 flex w-fit items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
    >
      <IoLogoSlack className="size-3.5" />
      Open in Slack
      <ArrowUpRight className="size-3" />
    </a>
  )
}

export function AgentThreadView({
  thread,
  autoFocusComposer = false,
}: AgentThreadViewProps) {
  const renameThread = useRenameAgentThread()
  const sendMessage = useSubmitAgentMessage(thread.id)
  const source = useThreadSource()
  const isMobile = useIsMobile()
  const skills = useAgentSkills()
  const session = useSession()
  const canPost =
    (thread.threadCategory !== "automation" && !thread.adminThread) ||
    session.data?.is_admin === true
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
  const [autoSelected, setAutoSelected] = useState(false)
  const activeSelection = autoSelected
    ? null
    : (selection ??
      (thread.modelSelection === "auto"
        ? null
        : (threadSelection ?? defaultSelection)))
  const handleSelectionChange = (next: ModelSelection | null) => {
    setAutoSelected(next === null)
    setSelection(next)
  }
  const [planMode, setPlanMode] = useState<boolean | null>(null)
  const [planFeedbackPending, setPlanFeedbackPending] =
    useState(autoFocusComposer)
  const scrollControlRef = useRef<MessagesScrollControl | null>(null)
  const activePlanMode = planMode ?? thread.planMode ?? false
  const routed = source.routed
  const activeModel = models.find(
    (model) => model.id === activeSelection?.modelId
  )
  const baseMessages = source.messages
  const isStreaming = thread.status === "running" || source.isRunning
  const queuedMessages = useQueuedMessages(thread.id)
  const sendInFlightRef = useRef(false)

  const submitMessage = useCallback(
    async (content: string, images: Array<ImageChunk>) => {
      scrollControlRef.current?.scrollToBottom()
      // While a run is live the draft waits for the next tool boundary; the
      // release effect below sends it through the same path as a direct send.
      if (isStreaming && content.trim() !== "/offload") {
        useQueuedMessageStore.getState().enqueue(thread.id, {
          text: content,
          images,
          modelId: activeSelection?.modelId ?? null,
          effort: activeSelection?.effort ?? null,
          planMode: activePlanMode,
          queuedAfterToolCallId: latestCompletedToolCallId(baseMessages),
          createdAt: Date.now(),
        })
        return
      }
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
      baseMessages,
      isStreaming,
      planFeedbackPending,
      sendMessage,
      thread.id,
    ]
  )

  // Sends one queued message. A rejected start hands it back to the head,
  // held, so the messages behind it keep their order and the user retries or
  // edits; unless Stop drained the queue in the meantime, in which case it
  // already went back to the composer and must not reappear.
  const sendQueuedMessage = useCallback(
    async (message: QueuedComposerMessage) => {
      if (sendInFlightRef.current) return
      const store = useQueuedMessageStore.getState()
      const taken = store.take(
        thread.id,
        message.id,
        latestCompletedToolCallId(baseMessages)
      )
      if (!taken) return
      const drainGenerationAtTake = store.drainGeneration
      const holdAgain = () => {
        if (
          useQueuedMessageStore.getState().drainGeneration ===
          drainGenerationAtTake
        ) {
          useQueuedMessageStore.getState().holdAtFront(thread.id, taken)
        }
      }
      sendInFlightRef.current = true
      try {
        await sendMessage.mutateAsync({
          content: taken.text,
          images: taken.images,
          model_id: taken.modelId,
          effort: taken.effort,
          plan_mode: taken.planMode,
          client_message_id: taken.id,
          onFailure: holdAgain,
        })
      } catch {
        holdAgain()
      } finally {
        sendInFlightRef.current = false
      }
    },
    [baseMessages, sendMessage, thread.id]
  )

  const [restoreDraft, setRestoreDraft] = useState<RestoredDraft | null>(null)
  const restoreQueuedMessagesToComposer = useCallback(
    (messages: ReadonlyArray<QueuedComposerMessage>) => {
      if (messages.length === 0) return
      setRestoreDraft((previous) => ({
        key: (previous?.key ?? 0) + 1,
        text: messages
          .map((message) => message.text.trim())
          .filter(Boolean)
          .join("\n\n"),
        images: messages.flatMap((message) => message.images),
      }))
    },
    []
  )
  const steerQueuedMessage = useCallback(
    (id: string) => {
      const message = queuedMessages.find((entry) => entry.id === id)
      if (message) void sendQueuedMessage(message)
    },
    [queuedMessages, sendQueuedMessage]
  )
  const removeQueuedMessage = useCallback(
    (id: string) => {
      const message = useQueuedMessageStore.getState().remove(thread.id, id)
      if (message) restoreQueuedMessagesToComposer([message])
    },
    [restoreQueuedMessagesToComposer, thread.id]
  )
  // Stop also cancels the queue: the messages return to the composer instead
  // of starting a new run the moment the interrupted one settles.
  const stopRun = useCallback(async () => {
    restoreQueuedMessagesToComposer(
      useQueuedMessageStore.getState().drain(thread.id)
    )
    await source.stop()
  }, [restoreQueuedMessagesToComposer, source, thread.id])
  const fixPullRequest = useCallback(
    async (pullRequest: AgentPullRequest) => {
      const result = await agentsApi.getThreadPullRequestContext(
        thread.id,
        pullRequest.repoFullName,
        pullRequest.number
      )
      await submitMessage(result.prompt, [])
    },
    [submitMessage, thread.id]
  )
  const usedTokens = source.contextTokens

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

  const activeRun = useMemo(
    () => ({ threadId: thread.id, running: thread.status === "running" }),
    [thread.id, thread.status]
  )
  const pendingMessages = useMemo(
    () => visiblePendingMessages(thread.pendingMessages, baseMessages),
    [baseMessages, thread.pendingMessages]
  )
  const visibleMessages = useMemo(
    () => [...baseMessages, ...pendingMessages],
    [baseMessages, pendingMessages]
  )
  const queuedRows = useMemo(
    () =>
      queuedMessages.map((message) => ({
        id: message.id,
        content: message.text,
        images: message.images,
        createdAt: message.createdAt,
        held: message.holdUntilUserAction === true,
      })),
    [queuedMessages]
  )

  // Sends the oldest queued message once it is due: a tool call finished
  // after it was queued, or the run ended. Only one leaves per boundary; the
  // take inside the send re-anchors the rest. "connecting" is the gap between
  // a send and the message showing up in the transcript, when nothing is due.
  const nextQueuedMessage = queuedMessages[0] ?? null
  const latestToolCallId = useMemo(
    () => (nextQueuedMessage ? latestCompletedToolCallId(baseMessages) : null),
    [baseMessages, nextQueuedMessage]
  )
  const queuePhase: QueuePhase =
    pendingMessages.length > 0
      ? "connecting"
      : isStreaming
        ? "running"
        : "ready"
  useEffect(() => {
    if (!nextQueuedMessage || sendInFlightRef.current) return
    if (
      !isQueuedMessageDue({
        message: nextQueuedMessage,
        phase: queuePhase,
        latestToolCallId,
      })
    )
      return
    void sendQueuedMessage(nextQueuedMessage)
  }, [latestToolCallId, nextQueuedMessage, queuePhase, sendQueuedMessage])

  const hasMessages = visibleMessages.length > 0
  const hasConversation = hasMessages || queuedMessages.length > 0
  // The only file list the UI has: whatever the agent has already touched in
  // this thread. Those are also the paths a follow-up is most likely about.
  const mentionPaths = useMemo(() => editedPaths(baseMessages), [baseMessages])
  const loadEarlier = useMemo<LoadEarlier | null>(
    () =>
      source.hasOlder
        ? { loading: source.isLoadingOlder, onLoadEarlier: source.loadOlder }
        : null,
    [source.hasOlder, source.isLoadingOlder, source.loadOlder]
  )
  const isThinking = source.isRunning
  const settingUpSandbox = isThinking && baseMessages.length === 0
  const reconnect = useConnectionStatus(source.connection)
  // The transcript hydrates once: the SDK's state fetch, or the event log's
  // snapshot. Show a loading state during it instead of the empty state.
  const isHydrating = source.isHydrating && !hasMessages
  const hydrationTimedOut = useLoadTimedOut(isHydrating)
  // A failed hydrate is indistinguishable from an empty thread in the snapshot,
  // so say so rather than claiming the thread has no messages. `source.error`
  // also carries run failures, hence the dedicated hydration signal.
  const [hydrateError, setHydrateError] = useState<unknown>(null)
  useEffect(() => {
    let active = true
    // oxlint-disable-next-line react/set-state-in-effect
    setHydrateError(null)
    source.hydration.catch((error: unknown) => {
      if (!active) return
      setHydrateError(error)
      threadHydrationFailed(thread.id)
    })
    return () => {
      active = false
    }
  }, [source.hydration, thread.id])
  const hydrationFailed = !hasMessages && hydrateError !== null

  useEffect(() => {
    if (!source.isHydrating) threadHydrated(thread.id)
  }, [source.isHydrating, thread.id])

  // The transcript's first frame: one rAF after the commit that replaced the
  // hydration placeholder. A commit before the frame fires cancels and
  // reschedules it, so the frame recorded is the one that actually reached the
  // screen; the ref is only set once it has.
  const paintedThreadId = useRef<string | null>(null)
  useLayoutEffect(() => {
    if (isHydrating || paintedThreadId.current === thread.id) return
    const messages = visibleMessages.length
    const chunks = visibleMessages.reduce((sum, m) => sum + m.chunks.length, 0)
    const frame = requestAnimationFrame(() => {
      paintedThreadId.current = thread.id
      threadTranscriptPainted(thread.id, { messages, chunks })
    })
    return () => cancelAnimationFrame(frame)
  }, [isHydrating, thread.id, visibleMessages])

  return (
    <div className="flex min-w-0 flex-1">
      <div
        className="flex min-w-0 flex-1 flex-col"
        style={isMobile ? undefined : { minWidth: SIBLING_COLUMN_MIN_WIDTH }}
      >
        <AgentThreadHeader
          key={thread.id}
          title={thread.title}
          onRename={(title) =>
            renameThread.mutateAsync({ threadId: thread.id, title })
          }
          target="Cloud"
          panelCollapsed={panelCollapsed}
          thread={thread}
        />
        {thread.status === "error" && !reconnect.label && (
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
        {thread.attentionReason === "prs_closed" && !thread.resolved && (
          <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pt-3">
            <Alert variant="info">
              <GitMergeIcon />
              <AlertDescription>
                <span>
                  Every pull request from this thread is merged or closed.
                  Resolve the thread if the work is done, or send a follow-up to
                  keep going.
                </span>
              </AlertDescription>
            </Alert>
          </div>
        )}
        <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
          {hydrationFailed || hydrationTimedOut ? (
            <LoadError
              title="Unable to load messages"
              context={`Thread: ${thread.id}`}
              error={
                hydrateError !== null
                  ? hydrateError
                  : "Message loading took longer than 30 seconds."
              }
            />
          ) : isHydrating ? (
            <div className="flex flex-1 items-center justify-center px-6">
              <img
                src={`${import.meta.env.BASE_URL}logo-mark.png`}
                alt="Loading conversation"
                className="size-12 animate-pulse"
              />
            </div>
          ) : (
            <PullRequestPreviewProvider
              pullRequests={thread.pullRequests ?? []}
              health={pullRequestHealth}
              healthUnavailable={pullRequestStatus.isError}
            >
              <Profiler
                id="transcript"
                onRender={(_id, _phase, actualDuration) =>
                  runTranscriptCommitted(thread.id, actualDuration)
                }
              >
                <Messages
                  messages={visibleMessages}
                  threadId={thread.id}
                  scrollKey={thread.id}
                  showPlanArtifact={
                    thread.planStatus === "ready" ||
                    thread.planStatus === "shared"
                  }
                  emptyState={
                    <div className="flex min-h-60 items-center justify-center">
                      {hydrationFailed ? (
                        <Alert variant="error" className="max-w-3xl">
                          <CircleAlertIcon />
                          <AlertDescription>
                            <span>
                              This thread&apos;s messages could not be loaded.
                              Reload to try again.
                            </span>
                          </AlertDescription>
                        </Alert>
                      ) : (
                        <p className="text-xs text-muted-foreground/70">
                          This thread has no messages yet.
                        </p>
                      )}
                    </div>
                  }
                  onOpenFile={handleOpenFile}
                  loadEarlier={loadEarlier}
                  queuedMessages={queuedRows}
                  onSteerQueuedMessage={steerQueuedMessage}
                  onRemoveQueuedMessage={removeQueuedMessage}
                  isStreaming={isStreaming}
                  streamIsLoading={source.isRunning}
                  scrollControlRef={scrollControlRef}
                  isThinking={isThinking}
                  isOffloading={source.isOffloading}
                  reconnectLabel={reconnect.label}
                  settingUpSandbox={settingUpSandbox}
                  pollWorkflowApprovalsWhileActive={isStreaming}
                  contentWidthClass="max-w-3xl"
                  footer={
                    !isStreaming &&
                    !sendMessage.isPending &&
                    queuedMessages.length === 0 && (
                      <ThreadFeedbackCard
                        key={`${thread.id}:${session.data?.login ?? ""}`}
                        threadId={thread.id}
                        login={session.data?.login ?? null}
                      />
                    )
                  }
                />
              </Profiler>
            </PullRequestPreviewProvider>
          )}
          {!isHydrating && (
            <AgentComposerDock>
              <CodeChannelLink url={thread.codeChannelUrl} />
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
                    ? hasConversation
                      ? "Add a follow up"
                      : "Send the first message"
                    : "Only workspace admins can send messages in this thread"
                }
                autoFocus={autoFocusComposer}
                canOffload={!isStreaming}
                compact
                disabled={!canPost}
                busy={isStreaming}
                activeRun={activeRun}
                onStop={stopRun}
                onSubmit={submitMessage}
                restoreDraft={restoreDraft}
                models={models}
                routed={routed}
                selection={activeSelection}
                onSelectionChange={handleSelectionChange}
                planMode={activePlanMode}
                onPlanModeChange={setPlanMode}
                mentionPaths={mentionPaths}
                skills={skills.data}
                contextUsage={{
                  usedTokens,
                  contextWindow: activeModel?.context_window ?? null,
                }}
              />
            </AgentComposerDock>
          )}
        </div>
      </div>
      <AgentGitPanel
        thread={thread}
        revealFilePath={revealFilePath}
        revealChangesKey={revealChangesKey}
        collapsed={panelCollapsed}
        onCollapsedChange={handlePanelCollapsedChange}
      />
    </div>
  )
}
