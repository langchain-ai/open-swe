import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { ChevronDown } from "lucide-react"

import { SkillPromptText } from "../SkillBadge"
import { AgentTurn } from "./timeline/AgentTurn"
import { liveActivityLabel } from "./timeline/workEntry"
import { ThinkingSpinner } from "./ThinkingSpinner"
import { UserMessage } from "./UserMessage"
import type { MessagesProps } from "./types"
import { TooltipProvider } from "@/components/ui/tooltip"
import { InlinePlanArtifact } from "@/features/agents/components/InlinePlanArtifact"
import { SelfReviewCard } from "@/features/agents/components/SelfReviewCard"
import { WorkflowApprovalCard } from "@/features/agents/components/WorkflowApprovalCard"
import { useLiveMarkdownMessageId } from "@/features/agents/lib/provider/useLiveMarkdownMessageId"

const BOTTOM_LOCK_THRESHOLD_PX = 24

function QueuedMessages({
  queuedMessages,
}: {
  queuedMessages: NonNullable<MessagesProps["queuedMessages"]>
}) {
  if (queuedMessages.length === 0) return null

  return (
    <div className="mb-3 space-y-2" data-testid="queued-messages">
      {queuedMessages.map((message, index) => {
        const imageCount = message.images?.length ?? 0
        return (
          <div
            key={message.id}
            className="ml-auto max-w-[85%] rounded-2xl border border-dashed border-border bg-accent/40 px-3 py-2 text-[14px] text-foreground shadow-sm"
            data-testid="queued-message"
          >
            <div className="mb-1 flex items-center gap-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
              <span>
                {queuedMessages.length > 1
                  ? `Queued next #${index + 1}`
                  : "Queued next"}
              </span>
              <span className="size-1.5 animate-status-pulse rounded-full bg-foreground/60" />
            </div>
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
          </div>
        )
      })}
    </div>
  )
}

export const Messages = memo(function MessagesComponent({
  messages,
  threadId,
  showPlanArtifact = false,
  showSelfReview = false,
  onOpenSelfReview,
  emptyState,
  pollWorkflowApprovalsWhileActive = false,
  queuedMessages = [],
  isStreaming,
  streamIsLoading,
  isThinking,
  settingUpSandbox,
  project,
  contentWidthClass = "max-w-[42rem]",
  contentPaddingClass = "px-6",
  bottomInset = 0,
  scrollButtonSlot = "internal",
  onShowScrollToBottomChange,
  scrollControlRef,
  onApprove,
  onReject,
  onAutoApprove,
  onOpenFile,
}: MessagesProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const autoScrollEnabledRef = useRef(true)
  const lastManualScrollTopRef = useRef(0)
  const previousScrollTopRef = useRef(0)
  const pendingScrollFrameRef = useRef<number | null>(null)
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)

  const clearScheduledScroll = useCallback(() => {
    if (pendingScrollFrameRef.current === null) return
    window.cancelAnimationFrame(pendingScrollFrameRef.current)
    pendingScrollFrameRef.current = null
  }, [])

  const isNearBottom = useCallback((el: HTMLDivElement) => {
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    return distanceFromBottom <= BOTTOM_LOCK_THRESHOLD_PX
  }, [])

  const syncScrollButtonVisibility = useCallback(
    (el: HTMLDivElement) => {
      setShowScrollToBottom(!isNearBottom(el))
    },
    [isNearBottom]
  )

  const scrollToBottomNow = useCallback(() => {
    const el = scrollRef.current
    if (!el) return

    el.scrollTop = el.scrollHeight
    const currentTop = el.scrollTop
    lastManualScrollTopRef.current = currentTop
    previousScrollTopRef.current = currentTop
    syncScrollButtonVisibility(el)
  }, [syncScrollButtonVisibility])

  const scheduleScrollToBottom = useCallback(() => {
    if (!autoScrollEnabledRef.current) return

    clearScheduledScroll()
    pendingScrollFrameRef.current = window.requestAnimationFrame(() => {
      pendingScrollFrameRef.current = null
      if (!autoScrollEnabledRef.current) return
      scrollToBottomNow()
    })
  }, [clearScheduledScroll, scrollToBottomNow])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return

    const handleScroll = () => {
      const currentTop = el.scrollTop
      const scrolledUp = currentTop < previousScrollTopRef.current - 1
      const nearBottom = isNearBottom(el)

      if (scrolledUp) {
        autoScrollEnabledRef.current = false
        clearScheduledScroll()
      } else if (nearBottom) {
        autoScrollEnabledRef.current = true
      }

      syncScrollButtonVisibility(el)
      lastManualScrollTopRef.current = currentTop
      previousScrollTopRef.current = currentTop
    }

    scrollToBottomNow()
    autoScrollEnabledRef.current = true

    el.addEventListener("scroll", handleScroll, { passive: true })
    return () => {
      el.removeEventListener("scroll", handleScroll)
      clearScheduledScroll()
    }
  }, [
    clearScheduledScroll,
    isNearBottom,
    scrollToBottomNow,
    syncScrollButtonVisibility,
  ])

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return

    if (autoScrollEnabledRef.current) {
      scheduleScrollToBottom()
      return
    }

    const maxTop = Math.max(0, el.scrollHeight - el.clientHeight)
    const targetTop = Math.min(lastManualScrollTopRef.current, maxTop)
    const jumpDistance = Math.abs(el.scrollTop - targetTop)

    if (jumpDistance > el.clientHeight * 0.5) {
      el.scrollTop = targetTop
    }

    previousScrollTopRef.current = el.scrollTop
    syncScrollButtonVisibility(el)
  }, [
    messages,
    isStreaming,
    scheduleScrollToBottom,
    syncScrollButtonVisibility,
  ])

  useEffect(() => {
    const scroller = scrollRef.current
    const content = contentRef.current
    if (!scroller || !content || typeof ResizeObserver === "undefined") return

    const resizeObserver = new ResizeObserver(() => {
      if (autoScrollEnabledRef.current) {
        scheduleScrollToBottom()
        return
      }

      const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight)
      if (lastManualScrollTopRef.current > maxTop) {
        scroller.scrollTop = maxTop
        lastManualScrollTopRef.current = maxTop
        previousScrollTopRef.current = maxTop
      }

      syncScrollButtonVisibility(scroller)
    })

    resizeObserver.observe(scroller)
    resizeObserver.observe(content)

    return () => resizeObserver.disconnect()
  }, [scheduleScrollToBottom, syncScrollButtonVisibility])

  const visibleMessages = useMemo(
    () => messages.filter((message) => !message.hidden),
    [messages]
  )
  const liveMarkdownMessageId = useLiveMarkdownMessageId(
    visibleMessages,
    streamIsLoading,
    isStreaming
  )

  const handleScrollToBottom = useCallback(() => {
    autoScrollEnabledRef.current = true
    clearScheduledScroll()
    scrollToBottomNow()
  }, [clearScheduledScroll, scrollToBottomNow])

  useEffect(() => {
    if (!scrollControlRef) return
    scrollControlRef.current = { scrollToBottom: handleScrollToBottom }
    return () => {
      scrollControlRef.current = null
    }
  }, [handleScrollToBottom, scrollControlRef])

  useEffect(() => {
    onShowScrollToBottomChange?.(showScrollToBottom)
  }, [onShowScrollToBottomChange, showScrollToBottom])

  const projectPath = project?.path
  const lastAgentIndex = visibleMessages.findLastIndex(
    (message) => message.author === "agent"
  )
  const activityLabel = useMemo(() => {
    if (!isStreaming) return undefined
    const lastMessage = visibleMessages.at(-1)
    if (!lastMessage || lastMessage.author !== "agent") return undefined
    return liveActivityLabel(lastMessage.chunks, projectPath)
  }, [isStreaming, projectPath, visibleMessages])

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
                  isStreaming={messageIsStreaming}
                  isMarkdownLive={messageIsMarkdownLive}
                  projectPath={projectPath}
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
            {threadId && showSelfReview && (
              <SelfReviewCard
                threadId={threadId}
                pollWhileActive={Boolean(isStreaming || streamIsLoading)}
                {...(onOpenSelfReview ? { onOpen: onOpenSelfReview } : {})}
              />
            )}
            {threadId && (
              <WorkflowApprovalCard
                threadId={threadId}
                pollWhileActive={pollWorkflowApprovalsWhileActive}
              />
            )}
            <QueuedMessages queuedMessages={queuedMessages} />
            <ThinkingSpinner
              isActive={
                !!(isThinking || streamIsLoading || isStreaming) &&
                !(
                  isStreaming &&
                  lastAgentIndex >= 0 &&
                  lastAgentIndex === visibleMessages.length - 1
                )
              }
              settingUpSandbox={settingUpSandbox}
              label={activityLabel}
            />
          </div>
        </div>

        {scrollButtonSlot === "internal" && showScrollToBottom && (
          <button
            type="button"
            onClick={handleScrollToBottom}
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
