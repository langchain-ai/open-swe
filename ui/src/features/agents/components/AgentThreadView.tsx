import { useCallback, useMemo, useState } from "react"
import { Link } from "@tanstack/react-router"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { Map as MapIcon } from "lucide-react"

import type {
  AgentThread,
  Message,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import {
  AgentGitPanel,
  PANEL_MIN_CHAT_WIDTH,
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/components/AgentGitPanel"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { WorkflowApprovalCard } from "@/features/agents/components/WorkflowApprovalCard"
import { Messages } from "@/features/agents/components/messages"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { useSubmitAgentMessage } from "@/features/agents/lib/provider/useSubmitAgentMessage"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

interface AgentThreadViewProps {
  thread: AgentThread
}

function messageText(message: Message): string {
  return message.chunks
    .map((chunk) => (chunk.kind === "text" ? chunk.text : ""))
    .join("\n")
    .trim()
}

function visibleQueuedMessages(
  queuedMessages: Array<QueuedThreadMessage> | undefined,
  messages: Array<Message>
): Array<QueuedThreadMessage> {
  const queued = queuedMessages ?? []
  if (queued.length === 0) return queued

  const userMessages = messages
    .filter((message) => message.author === "user")
    .map((message) => ({
      text: messageText(message),
      timestamp: Date.parse(message.timestamp),
      consumed: false,
    }))

  return queued.filter((queuedMessage) => {
    const queuedText = queuedMessage.content.trim()
    if (!queuedText) return true

    const match = userMessages.find((message) => {
      if (message.consumed || !message.text.includes(queuedText)) return false
      if (!Number.isFinite(message.timestamp)) return true
      return message.timestamp >= queuedMessage.createdAt - 1000
    })
    if (!match) return true

    match.consumed = true
    return false
  })
}

// The stream lives at the `/agents` layout (one persistent provider that
// survives the home → thread navigation), so this view only consumes it.
export function AgentThreadView({ thread }: AgentThreadViewProps) {
  const sendMessage = useSubmitAgentMessage(thread.id)
  const stream = useAgentThreadStream()
  const isMobile = useIsMobile()

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
  const activePlanMode = planMode ?? thread.planMode ?? false

  // Own the git panel's collapsed state so the plan banner can reserve space for
  // the floating expand button the panel renders while collapsed.
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed()
  )
  const handlePanelCollapsedChange = useCallback((next: boolean) => {
    setPanelCollapsed(next)
    writeStoredPanelCollapsed(next)
  }, [])

  const baseMessages = useMemo<Array<Message>>(() => {
    const live = streamMessagesToUi(
      stream.messages,
      stream.toolCalls,
      stream.subagents,
      messageArrivalTimestamp
    )
    if (live.length > 0) return live
    // Optimistic transcript seeded by `AgentsHome` on thread creation (the
    // only case where a fetched thread carries messages — `getThread` returns
    // none). Bridges the brief gap before the SDK's optimistic `submit` echo
    // lands in `stream.messages`.
    if (thread.messages.length > 0) return thread.messages
    return live
  }, [stream.messages, stream.toolCalls, stream.subagents, thread.messages])

  const isStreaming = thread.status === "running" || stream.isLoading
  const queuedMessages = useMemo(
    () => visibleQueuedMessages(thread.queuedMessages, baseMessages),
    [baseMessages, thread.queuedMessages]
  )
  const hasMessages = baseMessages.length > 0
  const hasConversation = hasMessages || queuedMessages.length > 0
  const isThinking = stream.isLoading
  const settingUpSandbox = isThinking && baseMessages.length === 0
  // The transcript hydrates from the SDK (`GET …/state` → `stream.messages`).
  // Show a loading state during that one-time fetch instead of the empty state.
  const isHydrating = stream.isThreadLoading && !hasMessages

  return (
    <div className="flex min-w-0 flex-1">
      <div
        className="flex min-w-0 flex-1 flex-col"
        style={isMobile ? undefined : { minWidth: PANEL_MIN_CHAT_WIDTH }}
      >
        {thread.status === "error" && (
          <div className="border-b border-[var(--ui-border)] bg-[var(--ui-danger)]/10 px-4 py-2 text-xs text-[var(--ui-danger)]">
            The last run hit an error before it could finish. Send another
            message to retry.
          </div>
        )}
        <WorkflowApprovalCard
          threadId={thread.id}
          pollWhileActive={isStreaming}
        />
        {thread.planStatus &&
          thread.planStatus !== "approved" &&
          thread.planStatus !== "cancelled" && (
            <Link
              to="/agents/$threadId/plan"
              params={{ threadId: thread.id }}
              data-testid="review-plan-link"
              className={cn(
                "flex items-center justify-between gap-2 border-b border-[var(--ui-border)] bg-[var(--ui-panel)] px-4 py-2 text-xs text-[var(--ui-text)] hover:bg-[var(--ui-panel-2)]",
                // The collapsed panel floats a fixed expand button in the
                // top-right corner; clear it so it never covers "Review plan →".
                panelCollapsed && "pr-14"
              )}
            >
              <span className="flex items-center gap-2">
                <MapIcon className="size-3.5 text-[var(--ui-accent)]" />
                {thread.planStatus === "ready"
                  ? "A plan is ready for your review."
                  : thread.planStatus === "shared"
                    ? "The agent shared a longer response."
                    : thread.planStatus === "revising"
                      ? "The agent is revising the plan."
                      : "The agent is writing a plan."}
              </span>
              <span className="font-medium text-[var(--ui-accent)]">
                {thread.planStatus === "shared"
                  ? "Open response →"
                  : "Review plan →"}
              </span>
            </Link>
          )}
        {hasConversation ? (
          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
            <Messages
              messages={baseMessages}
              queuedMessages={queuedMessages}
              isStreaming={isStreaming}
              streamIsLoading={stream.isLoading}
              isThinking={isThinking}
              settingUpSandbox={settingUpSandbox}
              contentWidthClass="max-w-3xl"
            />
            <div className="shrink-0 px-4 pb-4">
              <div className="mx-auto w-full max-w-3xl min-w-0">
                <AgentPromptBar
                  placeholder="Add a follow up"
                  compact
                  busy={isStreaming}
                  onSubmit={(content, images) =>
                    sendMessage.mutateAsync({
                      content,
                      images,
                      model_id: activeSelection?.modelId ?? null,
                      effort: activeSelection?.effort ?? null,
                      plan_mode: activePlanMode,
                    })
                  }
                  models={models}
                  selection={activeSelection}
                  onSelectionChange={setSelection}
                  planMode={activePlanMode}
                  onPlanModeChange={setPlanMode}
                />
              </div>
            </div>
          </div>
        ) : isHydrating ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6">
            <p className="text-xs text-[var(--ui-text-dim)]">
              Loading conversation…
            </p>
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6">
            <p className="text-xs text-[var(--ui-text-dim)]">
              This thread has no messages yet.
            </p>
            <div className="w-full max-w-3xl">
              <AgentPromptBar
                placeholder="Send the first message"
                compact
                busy={isStreaming}
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
              />
            </div>
          </div>
        )}
      </div>
      <AgentGitPanel
        thread={thread}
        messages={baseMessages}
        collapsed={panelCollapsed}
        onCollapsedChange={handlePanelCollapsedChange}
      />
    </div>
  )
}
