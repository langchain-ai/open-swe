import { memo, useEffect, useMemo } from "react"

import { SkillPromptText } from "../SkillBadge"
import { AgentTurn } from "./timeline/AgentTurn"
import { liveActivityLabel } from "./timeline/workEntry"
import { ThinkingSpinner } from "./ThinkingSpinner"
import { UserMessage } from "./UserMessage"
import { useTranscriptScroll } from "./useTranscriptScroll"
import type { MessagesProps } from "./types"
import { AgentThread } from "@langchain/gtm-platform-design-system/patterns/agent-thread"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { TooltipProvider } from "@langchain/gtm-platform-design-system/ui/tooltip"
import { ArrowUp, ChevronDown, Clock, X } from "@/components/glyphs"
import { InlinePlanArtifact } from "@/features/agents/components/InlinePlanArtifact"
import { WorkflowApprovalCard } from "@/features/agents/components/WorkflowApprovalCard"
import { useLiveMarkdownMessageId } from "@/features/agents/lib/provider/useLiveMarkdownMessageId"
import { cn } from "@/lib/utils"

const QUEUED_ACTION_CLASS = "text-ink-subtle hover:text-ink"

/**
 * Messages waiting for the run to end. They take the user turn's geometry on
 * a dashed line, because they are the user's words that have not been sent.
 */
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
    <Stack gap="sm" align="end" data-testid="queued-messages">
      {queuedMessages.map((message, index) => {
        const imageCount = message.images?.length ?? 0
        const statusLabel =
          index === 0
            ? "Sends when the run ends. Send now, or Enter on an empty composer, steers the run with it instead."
            : "Waits for the message ahead of it."
        return (
          <Stack
            key={message.id}
            gap="xs"
            className="max-w-lg min-w-0 rounded-panel rounded-br-none border border-dashed border-line-strong bg-muted px-3 py-2.5 text-body text-ink"
            data-testid="queued-message"
            data-queued-pending={message.pending ? "true" : "false"}
          >
            {message.content && (
              <Box className="break-words whitespace-pre-wrap">
                <SkillPromptText text={message.content} />
              </Box>
            )}
            {imageCount > 0 && (
              <Box render={<p />} className="text-meta text-ink-subtle">
                {imageCount} image{imageCount === 1 ? "" : "s"} attached
              </Box>
            )}
            <Inline
              gap="md"
              align="center"
              className="min-h-control-sm text-meta text-ink-subtle"
            >
              <Inline
                render={<span />}
                gap="xs"
                align="center"
                title={statusLabel}
                aria-label={`Queued. ${statusLabel}`}
              >
                <Icon icon={Clock} size="sm" />
                Queued
                <span className="ml-1 size-1.5 animate-status-pulse rounded-full bg-ink-subtle motion-reduce:animate-none" />
              </Inline>
              {(onSteer || onRemove) && message.mine !== false && (
                <Inline gap="none" align="center" className="ml-auto">
                  {onSteer && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className={QUEUED_ACTION_CLASS}
                      onPointerDown={(event) => event.preventDefault()}
                      onClick={() => onSteer(message.id)}
                      disabled={message.pending}
                      title="Send now"
                      aria-label="Send now"
                      data-testid="queued-message-send-now"
                    >
                      <Icon icon={ArrowUp} size="sm" />
                    </Button>
                  )}
                  {onRemove && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className={QUEUED_ACTION_CLASS}
                      onPointerDown={(event) => event.preventDefault()}
                      onClick={() => onRemove(message.id)}
                      disabled={message.pending}
                      title="Cancel and return to the composer"
                      aria-label="Cancel and return to the composer"
                      data-testid="queued-message-cancel"
                    >
                      <Icon icon={X} size="sm" />
                    </Button>
                  )}
                </Inline>
              )}
            </Inline>
          </Stack>
        )
      })}
    </Stack>
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
  contentWidthClass = "max-w-thread",
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
      <Box className="relative min-h-0 min-w-0 flex-1">
        <Box
          ref={scrollRef}
          // Gutter on both edges: the centered column keeps its position when the
          // scrollbar appears, so it stays aligned with the composer below it.
          style={{ scrollbarGutter: "stable both-edges" }}
          className="h-full min-h-0 min-w-0 overflow-x-hidden overflow-y-auto text-body"
        >
          <Box
            ref={contentRef}
            className="w-full min-w-0"
            style={bottomInset > 0 ? { paddingBottom: bottomInset } : undefined}
          >
            <AgentThread
              className={cn("min-w-0", contentWidthClass, contentPaddingClass)}
            >
              {loadEarlier && (
                <Button
                  type="button"
                  variant="ghost"
                  size="compact"
                  disabled={loadEarlier.loading}
                  onClick={() => {
                    capturePrependAnchor()
                    loadEarlier.onLoadEarlier()
                  }}
                  className="self-center text-ink-subtle hover:text-ink"
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
                const messageIsMarkdownLive =
                  message.id === liveMarkdownMessageId

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
                    activityLabel={
                      messageIsStreaming ? activityLabel : undefined
                    }
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
            </AgentThread>
          </Box>
        </Box>

        {scrollButtonSlot === "internal" && showScrollToBottom && (
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={scrollToBottom}
            aria-label="Scroll to bottom"
            className="absolute left-1/2 z-30 -translate-x-1/2 text-ink-subtle shadow-popup hover:text-ink"
            style={{ bottom: bottomInset > 0 ? bottomInset + 8 : 16 }}
          >
            <Icon icon={ChevronDown} size="sm" />
          </Button>
        )}
      </Box>
    </TooltipProvider>
  )
})
