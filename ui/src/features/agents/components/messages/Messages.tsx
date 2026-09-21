import { memo, useEffect, useMemo } from "react"
import { ArrowUp, ChevronDown, Clock, X } from "lucide-react"

import { SkillPromptText } from "../SkillBadge"
import { AgentTurn } from "./timeline/AgentTurn"
import { liveActivityLabel } from "./timeline/workEntry"
import { ThinkingSpinner } from "./ThinkingSpinner"
import { UserMessage } from "./UserMessage"
import { useTranscriptScroll } from "./useTranscriptScroll"
import type { MessagesProps } from "./types"
import { TooltipProvider } from "@/components/ui/tooltip"
import { InlinePlanArtifact } from "@/features/agents/components/InlinePlanArtifact"
import { WorkflowApprovalCard } from "@/features/agents/components/WorkflowApprovalCard"
import { useLiveMarkdownMessageId } from "@/features/agents/lib/provider/useLiveMarkdownMessageId"

function queuedStatusLabel(isNext: boolean, held: boolean): string {
  if (held) return "Waiting for you: send it now, or cancel to edit it."
  if (isNext) return "Sends when the run ends. Send now steers the run instead."
  return "Waits for the message ahead of it."
}

function QueuedMessages({
  queuedMessages,
  onSteer,
  onRemove,
}: {
  queuedMessages: NonNullable<MessagesProps["queuedMessages"]>
  onSteer?: (id: string) => void
  onRemove?: (id: string) => void
}) {
  if (queuedMessages.length === 0) return null

  return (
    <div className="mb-3 space-y-2" data-testid="queued-messages">
      {queuedMessages.map((message, index) => {
        const imageCount = message.images?.length ?? 0
        const isNext = index === 0
        const held = message.held === true
        const statusLabel = queuedStatusLabel(isNext, held)
        return (
          <div
            key={message.id}
            className="ml-auto max-w-[85%] rounded-2xl border border-dashed border-border bg-accent/40 px-3 py-2 text-[14px] text-foreground shadow-sm"
            data-testid="queued-message"
            data-queued-held={held || undefined}
          >
            {message.content && (
              <div className="break-words whitespace-pre-wrap">
                <SkillPromptText text={message.content} />
              </div>
            )}
            {imageCount > 0 && (
              <div className="mt-1 text-xs text-muted-foreground">
                {imageCount} image{imageCount === 1 ? "" : "s"} attached
              </div>
            )}
            <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
              <span
                className="inline-flex h-6 items-center gap-1"
                title={statusLabel}
                aria-label={`Queued. ${statusLabel}`}
              >
                <Clock className="size-3.5" aria-hidden />
                {held ? "Held" : "Queued"}
                {!held && (
                  <span className="ml-1 size-1.5 animate-status-pulse rounded-full bg-foreground/60" />
                )}
              </span>
              {(onSteer || onRemove) && (
                <div className="ml-auto flex items-center gap-0.5">
                  {onSteer && (
                    <button
                      type="button"
                      className="flex size-6 items-center justify-center rounded-md hover:bg-accent hover:text-foreground"
                      onPointerDown={(event) => event.preventDefault()}
                      onClick={() => onSteer(message.id)}
                      title="Send now"
                      aria-label="Send now"
                      data-testid="queued-message-send-now"
                    >
                      <ArrowUp className="size-3.5" aria-hidden />
                    </button>
                  )}
                  {onRemove && (
                    <button
                      type="button"
                      className="flex size-6 items-center justify-center rounded-md hover:bg-accent hover:text-foreground"
                      onPointerDown={(event) => event.preventDefault()}
                      onClick={() => onRemove(message.id)}
                      title="Cancel and return to the composer"
                      aria-label="Cancel and return to the composer"
                      data-testid="queued-message-cancel"
                    >
                      <X className="size-3.5" aria-hidden />
                    </button>
                  )}
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
  emptyState,
  footer,
  pollWorkflowApprovalsWhileActive = false,
  queuedMessages = [],
  onSteerQueuedMessage,
  onRemoveQueuedMessage,
  isStreaming,
  streamIsLoading,
  isThinking,
  settingUpSandbox,
  isOffloading = false,
  reconnectLabel = null,
  localRepo,
  contentWidthClass = "max-w-[42rem]",
  contentPaddingClass = "px-6",
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
    showScrollToBottom,
    scrollToBottom,
    capturePrependAnchor,
  } = useTranscriptScroll({ scrollKey, messages, isStreaming })

  const visibleMessages = useMemo(
    () => messages.filter((message) => !message.hidden),
    [messages]
  )
  const liveMarkdownMessageId = useLiveMarkdownMessageId(
    visibleMessages,
    streamIsLoading,
    isStreaming
  )

  useEffect(() => {
    if (!scrollControlRef) return
    scrollControlRef.current = { scrollToBottom }
    return () => {
      scrollControlRef.current = null
    }
  }, [scrollToBottom, scrollControlRef])

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
    <TooltipProvider delay={250} closeDelay={0}>
      <div className="relative min-h-0 min-w-0 flex-1">
        <div
          ref={scrollRef}
          // Gutter on both edges: the centered column keeps its position when the
          // scrollbar appears, so it stays aligned with the composer below it.
          className="h-full min-h-0 min-w-0 [scrollbar-gutter:stable_both-edges] overflow-x-hidden overflow-y-auto py-5 text-[14px] leading-[1.6] antialiased"
        >
          <div
            ref={contentRef}
            className={`w-full ${contentWidthClass} mx-auto min-w-0 ${contentPaddingClass}`}
            style={bottomInset > 0 ? { paddingBottom: bottomInset } : undefined}
          >
            {loadEarlier && (
              <button
                type="button"
                disabled={loadEarlier.loading}
                onClick={() => {
                  capturePrependAnchor()
                  loadEarlier.onLoadEarlier()
                }}
                className="mb-3 w-full py-1.5 text-center text-xs text-muted-foreground hover:text-foreground disabled:cursor-default"
              >
                {loadEarlier.loading
                  ? "Loading earlier turns…"
                  : "Load earlier turns"}
              </button>
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
                  onApprove={onApprove}
                  onReject={onReject}
                  onAutoApprove={onAutoApprove}
                  onOpenFile={onOpenFile}
                />
              )
            })}
            {threadId && showPlanArtifact && (
              <InlinePlanArtifact threadId={threadId} />
            )}
            {threadId && (
              <WorkflowApprovalCard
                threadId={threadId}
                pollWhileActive={pollWorkflowApprovalsWhileActive}
              />
            )}
            <QueuedMessages
              queuedMessages={queuedMessages}
              onSteer={onSteerQueuedMessage}
              onRemove={onRemoveQueuedMessage}
            />
            {footer}
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
          </div>
        </div>

        {scrollButtonSlot === "internal" && showScrollToBottom && (
          <button
            type="button"
            onClick={scrollToBottom}
            aria-label="Scroll to bottom"
            className="dropdown-glass absolute left-1/2 z-30 inline-flex size-8 -translate-x-1/2 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground"
            style={{ bottom: bottomInset > 0 ? bottomInset + 8 : 16 }}
          >
            <ChevronDown className="size-3.5" />
          </button>
        )}
      </div>
    </TooltipProvider>
  )
})
