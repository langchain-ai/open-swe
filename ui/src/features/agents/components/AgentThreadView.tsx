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
  Laptop as LaptopIcon,
  TriangleAlert as TriangleAlertIcon,
} from "lucide-react"
import { IoLogoSlack } from "react-icons/io5"
import { LoadError, useLoadTimedOut } from "@/components/LoadError"
import { formatRelativeTime } from "@/lib/utils"

import type {
  AgentPullRequest,
  AgentThread,
  ImageChunk,
  Message,
  ThreadFixScope,
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
import { createAutoSelectionIntent } from "@/features/agents/lib/autoSelectionIntent"
import {
  agentThreadKeys,
  useAgentSkills,
  useRenameAgentThread,
  useAgentThreadPullRequestStatus,
} from "@/features/agents/lib/queries"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  materializeImages,
  visiblePendingMessages,
} from "@/features/agents/lib/queuedMessages"
import type { QueuedTurn } from "@/features/agents/lib/transcript/reducer"
import type {
  RestoredDraft,
  SubmitOptions,
} from "@/features/agents/components/composer/ChatComposer"
import { agentsApi } from "@/features/agents/lib/api"
import {
  localThreadKeys,
  runsOnAMac,
  useLocalRepoRefs,
  useLocalThread,
  useLocalThreadPr,
} from "@/features/agents/lib/desktopLocal"
import { reportError } from "@/lib/errorReporting"
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

export function AgentThreadView({ thread }: AgentThreadViewProps) {
  const renameThread = useRenameAgentThread()
  const sendMessage = useSubmitAgentMessage(thread.id)
  const source = useThreadSource()
  const [dismissedWarning, setDismissedWarning] = useState("")
  const workspaceWarningKey = JSON.stringify([
    thread.id,
    source.kind === "transcript" ? source.workspaceStale : null,
  ])
  const isMobile = useIsMobile()
  const skills = useAgentSkills()
  const session = useSession()
  // A "This Mac" thread can only run where its checkout is: the Mac whose app
  // started it. Everywhere else it is read-only.
  const localThread = useLocalThread(thread.id)
  const runsElsewhere = runsOnAMac(thread) && !localThread
  const canPost =
    !runsElsewhere &&
    ((thread.threadCategory !== "automation" && !thread.adminThread) ||
      session.data?.is_admin === true)
  // The bridge outlives a thread view but not the app; serve the checkout
  // again for whatever this thread runs next, wherever that run is started.
  const localBridge = useQuery({
    queryKey: ["local-bridge", thread.id],
    queryFn: async () =>
      (await window.openSweDesktop?.ensureLocalBridge(thread.id)) ?? false,
    enabled: Boolean(localThread),
    retry: false,
    refetchOnWindowFocus: "always",
  })
  const refetchLocalBridge = localBridge.refetch
  const ensureLocalBridge = useCallback(async () => {
    if (!localThread) return
    const result = await refetchLocalBridge({ throwOnError: true })
    if (result.isError) throw result.error
  }, [localThread, refetchLocalBridge])
  // A "This Mac" thread works in a checkout other threads on this machine may
  // share, so its branch and worktree are shown, and switched, from here.
  const localRefsQuery = useLocalRepoRefs(localThread?.cwd)
  const localRepoRefs = localRefsQuery.data
  const refetchLocalRepoRefs = localRefsQuery.refetch
  const localWorktreePath = localThread?.worktreePath ?? null
  const localBranch = localThread
    ? (localRepoRefs.find((candidate) =>
        localWorktreePath
          ? candidate.worktreePath === localWorktreePath
          : candidate.current
      )?.name ?? null)
    : null
  const localPr = useLocalThreadPr(thread.id, Boolean(localThread)).data ?? null
  const queryClient = useQueryClient()
  const selectLocalBranch = useCallback(
    async (branch: string) => {
      try {
        await window.openSweDesktop?.setLocalBranch({
          threadId: thread.id,
          branch,
        })
      } catch (error) {
        reportError({ title: "Couldn't switch the thread's branch", error })
      }
      void queryClient.invalidateQueries({ queryKey: localThreadKeys.all })
      void queryClient.invalidateQueries({
        queryKey: localThreadKeys.pr(thread.id),
      })
      await refetchLocalRepoRefs()
    },
    [queryClient, refetchLocalRepoRefs, thread.id]
  )
  const bridgeError = localBridge.error
    ? localBridge.error instanceof Error
      ? localBridge.error.message
      : "This Mac could not be reached"
    : null
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
  const [autoIntent] = useState(createAutoSelectionIntent)
  const activeSelection = autoSelected
    ? null
    : (selection ??
      (thread.modelSelection === "auto"
        ? null
        : (threadSelection ?? defaultSelection)))
  const handleSelectionChange = (next: ModelSelection | null) => {
    setAutoSelected(next === null)
    setSelection(next)
    autoIntent.select(next === null)
  }
  const scrollControlRef = useRef<MessagesScrollControl | null>(null)
  const routed = source.routed
  const activeModel = models.find(
    (model) => model.id === activeSelection?.modelId
  )
  const baseMessages = source.messages
  const isStreaming =
    source.kind === "transcript"
      ? source.isRunning
      : thread.status === "running" || source.isRunning
  // Server truth: follow-ups queued behind the live run, from the transcript.
  const queued = source.queued
  const login = session.data?.login
  // Only its sender may act on a queued follow-up; the server enforces it too.
  const isOwnQueued = useCallback(
    (entry: QueuedTurn) => login !== undefined && entry.senderLogin === login,
    [login]
  )

  const followUpBehavior = session.data?.follow_up_behavior ?? "steer"
  const submitMessage = useCallback(
    async (
      content: string,
      images: Array<ImageChunk>,
      options?: SubmitOptions
    ) => {
      scrollControlRef.current?.scrollToBottom()
      // While a run is live the message either waits for it as a queued run of
      // its own, or goes straight in and steers it. The preference sets the
      // default; ⌘↵ flips it for one message.
      const queue =
        (followUpBehavior === "queue") !== (options?.alternate === true)
      const messageId = crypto.randomUUID()
      const carriesAutoSelection = autoIntent.claim(
        messageId,
        content.trim() !== "/offload" && (!isStreaming || queue)
      )
      const restoreAutoSelection = () => autoIntent.restore(messageId)
      try {
        await ensureLocalBridge()
        await sendMessage.mutateAsync({
          content,
          images,
          client_message_id: messageId,
          model_id: activeSelection?.modelId ?? null,
          effort: activeSelection?.effort ?? null,
          model_selection_changed: carriesAutoSelection,
          enqueue: isStreaming && queue,
          ...(carriesAutoSelection
            ? { onStartError: restoreAutoSelection }
            : {}),
        })
      } catch (error) {
        restoreAutoSelection()
        throw error
      }
    },
    [
      activeSelection,
      autoIntent,
      ensureLocalBridge,
      followUpBehavior,
      isStreaming,
      sendMessage,
    ]
  )

  const restoreQueuedAutoSelection = autoIntent.restore

  const queuedText = (entry: QueuedTurn) =>
    entry.message.chunks
      .flatMap((chunk) => (chunk.kind === "text" ? [chunk.text] : []))
      .join("\n")
      .trim()
  const queuedImages = (entry: QueuedTurn) =>
    entry.message.chunks.filter((chunk) => chunk.kind === "image")

  const [restoreDraft, setRestoreDraft] = useState<RestoredDraft | null>(null)
  const [droppedFiles, setDroppedFiles] = useState<{
    key: number
    files: Array<File>
  } | null>(null)
  const restoreQueuedToComposer = useCallback(
    (texts: ReadonlyArray<string>, images: Array<ImageChunk>) => {
      if (texts.length === 0 && images.length === 0) return
      setRestoreDraft((previous) => ({
        key: (previous?.key ?? 0) + 1,
        text: texts.filter(Boolean).join("\n\n"),
        images,
      }))
    },
    []
  )
  // The images of a queued follow-up live with its run. Fetch them before that
  // run is withdrawn: a fetch that fails leaves the follow-up queued, intact.
  const materializeQueuedImages = useCallback(
    async (entry: QueuedTurn): Promise<Array<ImageChunk> | null> => {
      const { images, failed } = await materializeImages(queuedImages(entry))
      if (failed === 0) return images
      toast.error(
        failed === 1
          ? "Couldn't load the queued message's image, so it stays queued."
          : `Couldn't load ${failed} of the queued message's images, so it stays queued.`
      )
      return null
    },
    []
  )
  // A "stream"-kind source's queue only reflects a cancel via `cancelQueued`
  // — never a lifecycle event, since the run never reached "running" —
  // otherwise the row lingers until the next hydrate.
  const withdrawQueued = useCallback(
    async (entry: QueuedTurn) => {
      if (entry.runId === null) return
      if (source.kind === "stream") {
        if (!(await source.cancelQueued(entry.turnId))) {
          throw new Error("The queued message could not be cancelled.")
        }
      } else {
        await agentsApi.cancelRun(thread.id, entry.runId)
      }
      restoreQueuedAutoSelection(entry.message.id)
    },
    [restoreQueuedAutoSelection, source, thread.id]
  )
  const steerInFlightRef = useRef(false)
  // Send now: the follow-up leaves the queue and goes into the live run.
  const steerQueued = useCallback(
    async (entry: QueuedTurn) => {
      if (steerInFlightRef.current || entry.runId === null) return
      steerInFlightRef.current = true
      try {
        const images = await materializeQueuedImages(entry)
        if (images === null) return
        await withdrawQueued(entry)
        sendMessage.mutate({ content: queuedText(entry), images })
      } catch (error) {
        reportError({ title: "Couldn't send the queued message now", error })
      } finally {
        steerInFlightRef.current = false
      }
    },
    [materializeQueuedImages, sendMessage, withdrawQueued]
  )
  const steerQueuedMessage = useCallback(
    (id: string) => {
      const entry = queued.find((candidate) => candidate.message.id === id)
      if (entry) void steerQueued(entry)
    },
    [queued, steerQueued]
  )
  // Enter on an empty composer sends the head of the queue now.
  const steerNextQueuedMessage = useCallback(() => {
    const entry = queued.find(
      (candidate) => candidate.runId !== null && isOwnQueued(candidate)
    )
    if (entry) void steerQueued(entry)
  }, [isOwnQueued, queued, steerQueued])
  const removeQueuedMessage = useCallback(
    (id: string) => {
      const entry = queued.find((candidate) => candidate.message.id === id)
      if (!entry || entry.runId === null) return
      void (async () => {
        const images = await materializeQueuedImages(entry)
        if (images === null) return
        await withdrawQueued(entry)
        restoreQueuedToComposer([queuedText(entry)], images)
      })().catch((error: unknown) =>
        reportError({ title: "Couldn't cancel the queued message", error })
      )
    },
    [materializeQueuedImages, queued, restoreQueuedToComposer, withdrawQueued]
  )
  // Stop cancels the queued runs along with the live one; the user's own come
  // back to the composer instead of starting the moment the run settles. Only
  // once the cancel is accepted: otherwise they are still queued. The image
  // fetch runs alongside the stop, which must not wait on it; a failed fetch
  // restores what it could and says what it lost.
  const stopRun = useCallback(async () => {
    const pending = queued.filter(isOwnQueued)
    // A follow-up sent to queue moments ago may not be back from the server
    // yet. Stop cancels its run all the same, so its draft comes back too.
    const known = new Set(pending.map((entry) => entry.message.id))
    const unacknowledged = (thread.pendingMessages ?? []).filter(
      (message) => message.queued && !known.has(message.id)
    )
    const materialized = materializeImages([
      ...pending.flatMap(queuedImages),
      ...unacknowledged.flatMap((message) => message.images ?? []),
    ])
    if (!(await source.stop())) return
    for (const entry of pending) restoreQueuedAutoSelection(entry.message.id)
    for (const message of unacknowledged) restoreQueuedAutoSelection(message.id)
    if (source.kind === "stream" && pending.length > 0) {
      // Same reasoning as withdrawQueued: syncs the adapter's queue store.
      await Promise.allSettled(
        pending.map((entry) => source.cancelQueued(entry.turnId))
      )
    }
    if (unacknowledged.length > 0) {
      const dropped = new Set(unacknowledged.map((message) => message.id))
      queryClient.setQueryData<AgentThread>(
        agentThreadKeys.detail(thread.id),
        (prev) =>
          prev && {
            ...prev,
            pendingMessages: prev.pendingMessages?.filter(
              (message) => !dropped.has(message.id)
            ),
          }
      )
    }
    const { images, failed } = await materialized
    restoreQueuedToComposer(
      [
        ...pending.map(queuedText),
        ...unacknowledged.map((message) => message.content),
      ],
      images
    )
    if (failed > 0) {
      toast.error(
        failed === 1
          ? "Couldn't load one queued image; it was not restored to the composer."
          : `Couldn't load ${failed} queued images; they were not restored to the composer.`
      )
    }
  }, [
    isOwnQueued,
    queryClient,
    queued,
    restoreQueuedAutoSelection,
    restoreQueuedToComposer,
    source,
    thread.id,
    thread.pendingMessages,
  ])
  const fixPullRequest = useCallback(
    async (pullRequest: AgentPullRequest, scope: ThreadFixScope) => {
      const result = await agentsApi.getThreadPullRequestContext(
        thread.id,
        pullRequest.repoFullName,
        pullRequest.number,
        scope
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
  // An optimistic row sent to queue renders as a queued row from the start,
  // so it never flashes as a sent message before the server confirms it.
  const pendingMessages = useMemo(
    () =>
      visiblePendingMessages(
        thread.pendingMessages?.filter((message) => !message.queued),
        [...baseMessages, ...queued.map((entry) => entry.message)]
      ),
    [baseMessages, queued, thread.pendingMessages]
  )
  const visibleMessages = useMemo(
    () => [...baseMessages, ...pendingMessages],
    [baseMessages, pendingMessages]
  )
  // An optimistic row has done its job once the transcript or the queue holds
  // its id. Dropping it then keeps a withdrawn queued turn from resurfacing it
  // as "Sending" after the transcript hides that turn.
  useEffect(() => {
    const pending = thread.pendingMessages
    if (!pending?.length) return
    const persisted = new Set(
      [...baseMessages, ...queued.map((entry) => entry.message)].map(
        (message) => message.id
      )
    )
    if (!pending.some((message) => persisted.has(message.id))) return
    queryClient.setQueryData<AgentThread>(
      agentThreadKeys.detail(thread.id),
      (prev) =>
        prev && {
          ...prev,
          pendingMessages: prev.pendingMessages?.filter(
            (message) => !persisted.has(message.id)
          ),
        }
    )
  }, [baseMessages, queryClient, queued, thread.id, thread.pendingMessages])

  const queuedRows = useMemo(() => {
    const known = new Set(queued.map((entry) => entry.message.id))
    return [
      ...queued.map((entry) => ({
        id: entry.message.id,
        content: queuedText(entry),
        images: queuedImages(entry),
        createdAt: Date.parse(entry.requestedAt),
        pending: entry.runId === null,
        mine: isOwnQueued(entry),
      })),
      ...(thread.pendingMessages ?? [])
        .filter((message) => message.queued && !known.has(message.id))
        .map((message) => ({
          id: message.id,
          content: message.content,
          images: message.images,
          createdAt: message.createdAt,
          pending: true,
        })),
    ]
  }, [isOwnQueued, queued, thread.pendingMessages])

  const hasMessages = visibleMessages.length > 0
  const hasConversation = hasMessages || queuedRows.length > 0
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
          target={
            localThread || thread.sandboxBridgeClient === "desktop"
              ? "This Mac"
              : thread.sandboxBridgeClient === "cli"
                ? "Local CLI"
                : "Cloud"
          }
          panelCollapsed={panelCollapsed}
          thread={thread}
        />
        {(runsElsewhere || bridgeError) && (
          <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pt-3">
            <Alert
              variant={bridgeError ? "error" : "info"}
              controlAlignment="first-line"
            >
              <LaptopIcon />
              <AlertDescription>
                <span>
                  {bridgeError
                    ? `This thread's checkout on This Mac can't be served: ${bridgeError}`
                    : "This thread runs in a checkout on another Mac. Open it in the Open SWE app there to continue it."}
                </span>
              </AlertDescription>
            </Alert>
          </div>
        )}
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
        {source.kind === "transcript" && source.workspaceStale && (
          <div
            hidden={dismissedWarning === workspaceWarningKey}
            className="mx-auto w-full max-w-3xl shrink-0 px-4 pt-3"
          >
            <Alert variant="warning">
              <TriangleAlertIcon />
              <AlertDescription>
                <span>
                  The {source.workspaceStale.workspaceName} workspace image this
                  sandbox started from{" "}
                  {source.workspaceStale.capturedAt
                    ? `was captured ${formatRelativeTime(Date.parse(source.workspaceStale.capturedAt))}`
                    : "has never been refreshed"}
                  , so its repositories may be out of date. Open SWE continued
                  anyway and is refreshing the image in the background.
                </span>
              </AlertDescription>
              <AlertAction>
                <button
                  type="button"
                  onClick={() => setDismissedWarning(workspaceWarningKey)}
                  className="rounded-md px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  Dismiss
                </button>
              </AlertAction>
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
        <div
          className="relative flex min-h-0 flex-1 flex-col overflow-hidden"
          onDragOver={(event) => {
            if (canPost && event.dataTransfer.types.includes("Files"))
              event.preventDefault()
          }}
          onDrop={(event) => {
            if (
              !canPost ||
              !event.dataTransfer.types.includes("Files") ||
              (event.target instanceof Element &&
                event.target.closest("[data-chat-composer]"))
            )
              return
            event.preventDefault()
            const files = Array.from(event.dataTransfer.files).filter((file) =>
              ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(
                file.type
              )
            )
            if (files.length)
              setDroppedFiles((previous) => ({
                key: (previous?.key ?? 0) + 1,
                files,
              }))
          }}
        >
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
                  showPlanArtifact={Boolean(thread.planStatus)}
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
                  onSteerQueuedMessage={
                    canPost ? steerQueuedMessage : undefined
                  }
                  onRemoveQueuedMessage={
                    canPost ? removeQueuedMessage : undefined
                  }
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
                    queuedRows.length === 0 && (
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
                pullRequests={
                  // A local branch's PR may come from `gh`, which the thread
                  // record never hears about.
                  thread.pullRequests?.length
                    ? thread.pullRequests
                    : localPr
                      ? [localPr]
                      : []
                }
                health={pullRequestHealth}
                healthUnavailable={pullRequestStatus.isError}
                onFix={fixPullRequest}
                fixDisabled={!canPost || sendMessage.isPending}
              />
              <AgentPromptBar
                placeholder={
                  runsElsewhere
                    ? "This thread runs on another Mac"
                    : canPost
                      ? hasConversation
                        ? "Add a follow up"
                        : "Send the first message"
                      : "Only workspace admins can send messages in this thread"
                }
                canOffload={!isStreaming}
                compact
                disabled={!canPost}
                busy={isStreaming}
                activeRun={activeRun}
                onStop={stopRun}
                onSubmit={submitMessage}
                onEmptySubmit={steerNextQueuedMessage}
                followUpBehavior={followUpBehavior}
                restoreDraft={restoreDraft}
                droppedFiles={droppedFiles}
                models={models}
                routed={routed}
                selection={activeSelection}
                onSelectionChange={handleSelectionChange}
                mentionPaths={mentionPaths}
                skills={skills.data}
                {...(localThread
                  ? {
                      runTarget: "local" as const,
                      selectedLocalRepoPath: localThread.cwd,
                      localRepoBranches: localRepoRefs,
                      selectedLocalRepoBranch: localBranch,
                      onRefreshLocalRepoBranch: () =>
                        void refetchLocalRepoRefs(),
                      onSelectLocalRepoBranch: (branch: string) =>
                        void selectLocalBranch(branch),
                      localWorkspaceMode: localWorktreePath
                        ? ("worktree" as const)
                        : ("local" as const),
                      localWorktreeLabel: "Worktree",
                    }
                  : {})}
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
