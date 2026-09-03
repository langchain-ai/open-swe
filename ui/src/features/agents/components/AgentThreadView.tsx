import { useCallback, useEffect, useMemo, useState } from "react"
import { ArrowUpRight, CircleAlert as CircleAlertIcon } from "lucide-react"
import { IoLogoSlack } from "react-icons/io5"

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
  useAgentSkills,
  useAgentThreadPullRequestStatus,
} from "@/features/agents/lib/queries"
import { visibleQueuedMessages } from "@/features/agents/lib/queuedMessages"
import { agentsApi } from "@/features/agents/lib/api"
import { rejectPlan } from "@/lib/plan"
import { useSession } from "@/lib/session"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"
import { useAgentThreadRuntime } from "@/features/agents/lib/AgentThreadStreamProvider"

interface AgentThreadViewProps {
  thread: AgentThread
  autoFocusComposer?: boolean
}

const EMPTY_MESSAGES: Array<Message> = []

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
      Open code channel
      <ArrowUpRight className="size-3" />
    </a>
  )
}

// The stream lives at the `/agents` layout (one persistent provider that
// survives the home → thread navigation), so this view only consumes it.
export function AgentThreadView({
  thread,
  autoFocusComposer = false,
}: AgentThreadViewProps) {
  const sendMessage = useSubmitAgentMessage(thread.id)
  const stream = useAgentThreadRuntime()
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

  const snapshotMessages =
    thread.messages.length > 0 ? thread.messages : EMPTY_MESSAGES
  const baseMessages = useMemo<Array<Message>>(() => {
    if (snapshotMessages.length > 0) return snapshotMessages
    return streamMessagesToUi(
      stream.messages,
      stream.toolCalls,
      messageArrivalTimestamp
    )
  }, [snapshotMessages, stream.messages, stream.toolCalls])

  const isStreaming =
    thread.status === "running" ||
    stream.isLoading ||
    thread.messages.length > 0
  const activeRun = useMemo(
    () => ({ threadId: thread.id, running: thread.status === "running" }),
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
        <AgentThreadHeader
          project={thread.repoFullName}
          target="Cloud"
          panelCollapsed={panelCollapsed}
        />
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
        <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
          {hasConversation ? (
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
          ) : isHydrating ? (
            <div className="flex flex-1 items-center justify-center px-6">
              <img
                src="/logo-mark.png"
                alt="Loading conversation"
                className="size-12 animate-pulse"
              />
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 items-center justify-center px-6">
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
            </div>
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
                compact
                disabled={!canPost}
                busy={isStreaming}
                activeRun={activeRun}
                draftKey={thread.id}
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
