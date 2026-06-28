import { useCallback, useMemo, useState } from "react"
import { Link } from "@tanstack/react-router"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import {
  ExternalLink,
  GitBranch,
  GitPullRequest,
  Map as MapIcon,
  ShieldCheck,
} from "lucide-react"
import type { ReactNode } from "react"

import type { AgentThread, Message } from "@/lib/agents/types"
import type { ModelSelection } from "@/lib/agents/provider/useModelOptions"
import {
  AgentGitPanel,
  PANEL_MIN_CHAT_WIDTH,
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/components/agents/AgentGitPanel"
import { AgentPromptBar } from "@/components/agents/AgentPromptBar"
import { Messages } from "@/components/agents/messages"
import { streamMessagesToUi } from "@/lib/agents/streamMessagesToUi"
import { messageArrivalTimestamp } from "@/lib/agents/messageTimestamps"
import { useSubmitAgentMessage } from "@/lib/agents/provider/useSubmitAgentMessage"
import { useModelOptions } from "@/lib/agents/provider/useModelOptions"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

interface AgentThreadViewProps {
  thread: AgentThread
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

  const hasMessages = baseMessages.length > 0
  const isStreaming = thread.status === "running" || stream.isLoading
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
                  : thread.planStatus === "revising"
                    ? "The agent is revising the plan."
                    : "The agent is writing a plan."}
              </span>
              <span className="font-medium text-[var(--ui-accent)]">
                Review plan →
              </span>
            </Link>
          )}
        {thread.delivery && <DeliveryRunPanel delivery={thread.delivery} />}
        {hasMessages ? (
          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
            <Messages
              messages={baseMessages}
              isStreaming={isStreaming}
              streamIsLoading={stream.isLoading}
              isThinking={isThinking}
              settingUpSandbox={settingUpSandbox}
              contentWidthClass="max-w-3xl"
            />
            <div className="shrink-0 px-4 pb-4">
              <div className="mx-auto w-full min-w-0 max-w-3xl">
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
            <p className="text-xs text-[var(--ui-text-dim)]">Loading conversation…</p>
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

type DeliveryRun = NonNullable<AgentThread["delivery"]>

function toneClass(value: string | null | undefined): string {
  const status = value?.toLowerCase()
  if (!status) return "border-[var(--ui-border)] text-[var(--ui-text-dim)]"
  if (["blocked", "failed", "failure", "error"].includes(status)) {
    return "border-[var(--ui-danger)]/35 text-[var(--ui-danger)]"
  }
  if (["review", "passed", "success", "merged", "done"].includes(status)) {
    return "border-[var(--ui-success)]/35 text-[var(--ui-success)]"
  }
  return "border-[var(--ui-border)] text-[var(--ui-text-muted)]"
}

function shortId(value: string): string {
  return value.length > 12 ? value.slice(0, 12) : value
}

function DeliveryPill({
  children,
  tone,
  title,
}: {
  children: ReactNode
  tone?: string
  title?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex min-h-6 max-w-full items-center gap-1 rounded-md border bg-[var(--ui-panel)] px-2 text-[11px] leading-5",
        tone
      )}
      title={title}
    >
      {children}
    </span>
  )
}

function DeliveryRunPanel({ delivery }: { delivery: DeliveryRun }) {
  const deliveryThreads = [
    ["Worker", delivery.workerThreadId],
    ["Review", delivery.reviewerThreadId],
    ["QA", delivery.qaThreadId],
    ["Merge", delivery.mergeWorkerThreadId],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]))
  const gateLabel = delivery.gateRollup
    ? `${delivery.gateRollup.status} ${delivery.gateRollup.passed}/${delivery.gateRollup.total}`
    : "unknown"

  return (
    <section className="border-b border-[var(--ui-border)] bg-[var(--ui-panel)] px-4 py-2">
      <div className="mx-auto flex max-w-3xl flex-col gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-[11px] text-[var(--ui-text-dim)]">
          <DeliveryPill tone={toneClass(delivery.queueStatus)}>
            Queue {delivery.queueStatus ?? "unknown"}
          </DeliveryPill>
          {delivery.branch && (
            <DeliveryPill title={delivery.branch}>
              <GitBranch className="size-3" />
              <span className="max-w-56 truncate">{delivery.branch}</span>
            </DeliveryPill>
          )}
          {delivery.pr && (
            <a
              href={delivery.pr.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-6 max-w-full items-center gap-1 rounded-md border border-[var(--ui-border)] bg-[var(--ui-panel)] px-2 text-[11px] leading-5 text-[var(--ui-text-muted)] hover:text-[var(--ui-text)]"
            >
              <GitPullRequest className="size-3" />
              PR #{delivery.pr.number}
              <ExternalLink className="size-3" />
            </a>
          )}
          {delivery.previewUrl && (
            <a
              href={delivery.previewUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-6 max-w-full items-center gap-1 rounded-md border border-[var(--ui-border)] bg-[var(--ui-panel)] px-2 text-[11px] leading-5 text-[var(--ui-text-muted)] hover:text-[var(--ui-text)]"
            >
              Preview
              <ExternalLink className="size-3" />
            </a>
          )}
          <DeliveryPill tone={toneClass(delivery.gateRollup?.status)}>
            <ShieldCheck className="size-3" />
            Gates {gateLabel}
          </DeliveryPill>
          {delivery.mergeStatus && (
            <DeliveryPill tone={toneClass(delivery.mergeStatus)}>
              Merge {delivery.mergeStatus}
            </DeliveryPill>
          )}
          {delivery.reviewedSha && (
            <DeliveryPill title={delivery.reviewedSha}>
              Reviewed {shortId(delivery.reviewedSha)}
            </DeliveryPill>
          )}
          {deliveryThreads.map(([label, threadId]) => (
            <DeliveryPill key={label} title={threadId}>
              {label} {shortId(threadId)}
            </DeliveryPill>
          ))}
          {delivery.artifactCount > 0 && (
            <DeliveryPill>{delivery.artifactCount} artifacts</DeliveryPill>
          )}
        </div>
        {delivery.blockerReason && (
          <div className="truncate text-[11px] text-[var(--ui-danger)]">
            Blocked: {delivery.blockerReason}
          </div>
        )}
      </div>
    </section>
  )
}
