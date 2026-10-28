import { Button } from "@langchain/macaw-components/Button"
import { CaretDownIcon, XIcon } from "@langchain/macaw-components/icons"
import { memo, useEffect, useMemo } from "react"
import { ReviewChatActions } from "@/features/reviews/components/ReviewChatActions"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"

import { SkillPromptText } from "../SkillBadge"
import { AgentTurn } from "./timeline/AgentTurn"
import { liveActivityLabel } from "./timeline/workEntry"
import { ThinkingSpinner } from "./ThinkingSpinner"
import { UserMessage } from "./UserMessage"
import { useTranscriptScroll } from "./useTranscriptScroll"
import { turnCosts } from "@/features/agents/lib/contextUsage"
import type { MessagesProps } from "./types"
import { SelectionReply } from "@/features/agents/components/SelectionReply"
import { InlinePlanArtifact } from "@/features/agents/components/InlinePlanArtifact"
import { WorkflowApprovalCard } from "@/features/agents/components/WorkflowApprovalCard"
import { useLiveMarkdownMessageId } from "@/features/agents/lib/provider/useLiveMarkdownMessageId"

function QueuedMessages({
  queuedMessages,
  onRemove,
}: {
  queuedMessages: NonNullable<MessagesProps["queuedMessages"]>
  onRemove?: (id: string) => void
}) {
  if (queuedMessages.length === 0) return null

  return (
    <div className="mb-space-3 space-y-space-2" data-testid="queued-messages">
      {queuedMessages.map((message, index) => {
        const imageCount = message.images?.length ?? 0
        const statusLabel = message.waitsForAgent
          ? "Held for the agent. It reads this before its next step, and it starts no run of its own."
          : index === 0
            ? "Sends when the run ends."
            : "Waits for the message ahead of it."
        const status = message.waitsForAgent
          ? "Waiting for the agent"
          : "Queued"
        const actionable =
          onRemove && message.mine !== false && !message.waitsForAgent
        return (
          <div
            key={message.id}
            className="ml-auto max-w-[85%] rounded-xl border border-dashed border-default bg-surface-level-2 px-space-3 py-space-2 text-sm text-primary shadow-sm"
            data-testid="queued-message"
            data-queued-pending={message.pending ? "true" : "false"}
            data-waits-for-agent={message.waitsForAgent ? "true" : undefined}
          >
            {message.content && (
              <div className="break-words whitespace-pre-wrap">
                <SkillPromptText text={message.content} />
              </div>
            )}
            {imageCount > 0 && (
              <div className="mt-space-1 text-xs text-secondary">
                {imageCount} image{imageCount === 1 ? "" : "s"} attached
              </div>
            )}
            <div className="mt-space-2 flex items-center gap-space-3 text-xs text-secondary">
              <span
                className="inline-flex h-6 items-center gap-space-1"
                title={statusLabel}
                aria-label={`${status}. ${statusLabel}`}
              >
                <ClockIcon size={14} weight="regular" aria-hidden />
                {status}
                <span className="ml-space-1 size-1.5 animate-status-pulse rounded-full bg-current" />
              </span>
              {message.sender && <span>from {message.sender}</span>}
              {actionable && (
                <div className="ml-auto flex items-center gap-0.5">
                  <IconButton
                    icon={XIcon}
                    label="Cancel and return to the composer"
                    size="sm"
                    color="secondary"
                    variant="plain"
                    onPointerDown={(event) => event.preventDefault()}
                    onClick={() => onRemove(message.id)}
                    disabled={message.pending}
                    data-testid="queued-message-cancel"
                  />
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export const Messages = memo(function MessagesComponent({
  messages,
  threadId,
  scrollKey,
  showPlanArtifact = false,
  runCosts,
  emptyState,
  footer,
  pollWorkflowApprovalsWhileActive = false,
  queuedMessages = [],
  onRemoveQueuedMessage,
  onReply,
  isStreaming,
  streamIsLoading,
  isThinking,
  settingUpSandbox,
  isOffloading = false,
  reconnectLabel = null,
  localRepo,
  contentWidthClass = "max-w-[42rem]",
  contentPaddingClass = "px-space-5",
  bottomInset = 0,
  loadEarlier = null,
  scrollButtonSlot = "internal",
  onShowScrollToBottomChange,
  scrollControlRef,
  onApprove,
  onReject,
  onAutoApprove,
  onOpenFile,
}: MessagesProps) {
  const {
    scrollRef,
    contentRef,
    reserveRef,
    showScrollToBottom,
    scrollToBottom,
    pinMessage,
    capturePrependAnchor,
  } = useTranscriptScroll({ scrollKey, messages, isStreaming })

  const visibleMessages = useMemo(
    () => messages.filter((message) => !message.hidden),
    [messages]
  )
  const costsByTurn = useMemo(
    () => turnCosts(visibleMessages, runCosts),
    [visibleMessages, runCosts]
  )
  const liveMarkdownMessageId = useLiveMarkdownMessageId(
    visibleMessages,
    streamIsLoading,
    isStreaming
  )

  useEffect(() => {
    if (!scrollControlRef) return
    scrollControlRef.current = { scrollToBottom, pinMessage }
    return () => {
      scrollControlRef.current = null
    }
  }, [pinMessage, scrollToBottom, scrollControlRef])

  useEffect(() => {
    onShowScrollToBottomChange?.(showScrollToBottom)
  }, [onShowScrollToBottomChange, showScrollToBottom])

  const repoPath = localRepo?.path
  const lastAgentIndex = visibleMessages.findLastIndex(
    (message) => message.author === "agent"
  )
  const activityLabel = useMemo(() => {
    if (!isStreaming) return undefined
    const lastMessage = visibleMessages.at(-1)
    if (!lastMessage || lastMessage.author !== "agent") return undefined
    return liveActivityLabel(lastMessage.chunks, repoPath)
  }, [isStreaming, repoPath, visibleMessages])

  return (
    <TooltipProvider delayDuration={250}>
      <div className="relative min-h-0 min-w-0 flex-1">
        <div
          ref={scrollRef}
          // Gutter on both edges: the centered column keeps its position when the
          // scrollbar appears, so it stays aligned with the composer below it.
          className="h-full min-h-0 min-w-0 [scrollbar-gutter:stable_both-edges] overflow-x-hidden overflow-y-auto py-space-5 text-sm leading-[1.6] antialiased"
        >
          <div
            ref={contentRef}
            className={`w-full ${contentWidthClass} mx-auto min-w-0 ${contentPaddingClass}`}
            style={bottomInset > 0 ? { paddingBottom: bottomInset } : undefined}
          >
            {loadEarlier && (
              <Button
                color="secondary"
                variant="plain"
                size="sm"
                aria-label={
                  loadEarlier.loading
                    ? "Loading earlier turns…"
                    : "Load earlier turns"
                }
                type="button"
                disabled={loadEarlier.loading}
                onClick={() => {
                  capturePrependAnchor()
                  loadEarlier.onLoadEarlier()
                }}
                className="mb-space-3 w-full py-space-1 text-center text-secondary hover:text-primary focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none disabled:cursor-default"
              >
                {loadEarlier.loading
                  ? "Loading earlier turns…"
                  : "Load earlier turns"}
              </Button>
            )}
            {visibleMessages.length === 0 && emptyState}
            {visibleMessages.map((message, index) => {
              const isLastMessage = index === visibleMessages.length - 1
              const messageIsStreaming = isStreaming && isLastMessage
              const messageIsMarkdownLive = message.id === liveMarkdownMessageId

              if (
                message.author === "user" ||
                message.structuredSenderKind === "system"
              ) {
                return <UserMessage key={message.id} message={message} />
              }

              return (
                <AgentTurn
                  key={message.id}
                  message={message}
                  isStreaming={messageIsStreaming && !isOffloading}
                  isMarkdownLive={messageIsMarkdownLive}
                  repoPath={repoPath}
                  activityLabel={messageIsStreaming ? activityLabel : undefined}
                  costUsd={costsByTurn.get(message.id)}
                  onApprove={onApprove}
                  onReject={onReject}
                  onAutoApprove={onAutoApprove}
                  onOpenFile={onOpenFile}
                />
              )
            })}
            <ReviewChatActions messages={visibleMessages} />
            {threadId && showPlanArtifact && (
              <InlinePlanArtifact threadId={threadId} />
            )}
            {threadId && (
              <WorkflowApprovalCard
                threadId={threadId}
                pollWhileActive={pollWorkflowApprovalsWhileActive}
              />
            )}
            {/* Above the queue: the live run's activity, then what waits for it. */}
            <ThinkingSpinner
              isActive={
                !!reconnectLabel ||
                isOffloading ||
                (!!(isThinking || streamIsLoading || isStreaming) &&
                  !(
                    isStreaming &&
                    lastAgentIndex >= 0 &&
                    lastAgentIndex === visibleMessages.length - 1
                  ))
              }
              settingUpSandbox={
                settingUpSandbox && !isOffloading && !reconnectLabel
              }
              label={
                reconnectLabel ??
                (isOffloading ? "Offloading conversation..." : activityLabel)
              }
            />
            <QueuedMessages
              queuedMessages={queuedMessages}
              onRemove={onRemoveQueuedMessage}
            />
            {footer}
          </div>
          <div ref={reserveRef} aria-hidden />
        </div>

        {onReply && (
          <SelectionReply containerRef={scrollRef} onReply={onReply} />
        )}

        {scrollButtonSlot === "internal" && showScrollToBottom && (
          <IconButton
            icon={CaretDownIcon}
            label="Scroll to bottom"
            size="md"
            round
            color="secondary"
            variant="outlined"
            onClick={scrollToBottom}
            className="absolute left-1/2 z-floating-bar -translate-x-1/2 shadow-md"
            style={{ bottom: bottomInset > 0 ? bottomInset + 8 : 16 }}
          />
        )}
      </div>
    </TooltipProvider>
  )
})
