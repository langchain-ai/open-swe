import { memo, useMemo } from "react"
import { ChevronDown } from "lucide-react"

import { SkillPromptText } from "../SkillBadge"
import { AgentTurn } from "./timeline/AgentTurn"
import { liveActivityLabel } from "./timeline/workEntry"
import { ThinkingSpinner } from "./ThinkingSpinner"
import { UserMessage } from "./UserMessage"
import type { MessagesProps } from "./types"
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation"
import {
  Queue,
  QueueItem,
  QueueItemAttachment,
  QueueItemContent,
  QueueItemImage,
  QueueItemIndicator,
  QueueList,
  QueueSection,
  QueueSectionContent,
  QueueSectionLabel,
  QueueSectionTrigger,
} from "@/components/ai-elements/queue"
import { TooltipProvider } from "@/components/ui/tooltip"
import { InlinePlanArtifact } from "@/features/agents/components/InlinePlanArtifact"
import { useLiveMarkdownMessageId } from "@/features/agents/lib/provider/useLiveMarkdownMessageId"

function QueuedMessages({
  queuedMessages,
}: {
  queuedMessages: NonNullable<MessagesProps["queuedMessages"]>
}) {
  if (queuedMessages.length === 0) return null

  return (
    <Queue
      className="my-3 ml-auto w-full max-w-[85%]"
      data-testid="queued-messages"
    >
      <QueueSection>
        <QueueSectionTrigger>
          <QueueSectionLabel
            count={queuedMessages.length}
            label={queuedMessages.length === 1 ? "queued next" : "queued"}
            icon={
              <span className="size-1.5 animate-status-pulse rounded-full bg-foreground/60" />
            }
          />
        </QueueSectionTrigger>
        <QueueSectionContent>
          <QueueList>
            {queuedMessages.map((message) => (
              <QueueItem key={message.id} data-testid="queued-message">
                <span className="flex items-start gap-2">
                  <QueueItemIndicator />
                  <QueueItemContent className="line-clamp-4 whitespace-pre-wrap text-foreground">
                    {message.content && (
                      <SkillPromptText text={message.content} />
                    )}
                  </QueueItemContent>
                </span>
                {message.images && message.images.length > 0 && (
                  <QueueItemAttachment className="ml-5">
                    {message.images.map((image, index) => (
                      <QueueItemImage
                        key={`${image.fileName ?? "image"}-${index}`}
                        alt={image.fileName ?? "Queued image"}
                        src={`data:${image.mimeType};base64,${image.base64}`}
                      />
                    ))}
                  </QueueItemAttachment>
                )}
              </QueueItem>
            ))}
          </QueueList>
        </QueueSectionContent>
      </QueueSection>
    </Queue>
  )
}

export const Messages = memo(function MessagesComponent({
  messages,
  threadId,
  showPlanArtifact = false,
  queuedMessages = [],
  isStreaming,
  streamIsLoading,
  isThinking,
  settingUpSandbox,
  project,
  contentWidthClass = "max-w-[42rem]",
  onOpenFile,
}: MessagesProps) {
  const visibleMessages = useMemo(
    () => messages.filter((message) => !message.hidden),
    [messages]
  )
  const liveMarkdownMessageId = useLiveMarkdownMessageId(
    visibleMessages,
    streamIsLoading,
    isStreaming
  )

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
      <Conversation
        // Gutter on both edges: the centered column keeps its position when the
        // scrollbar appears, so it stays aligned with the composer below it.
        className="min-h-0 min-w-0 [scrollbar-gutter:stable_both-edges] overflow-x-hidden text-[14px] leading-[1.6] antialiased"
      >
        <ConversationContent
          className={`w-full ${contentWidthClass} mx-auto min-w-0 gap-0 px-6 py-5`}
        >
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
                threadId={threadId}
                isLatestTurn={index === lastAgentIndex}
                activityLabel={messageIsStreaming ? activityLabel : undefined}
                onOpenFile={onOpenFile}
              />
            )
          })}
          {threadId && showPlanArtifact && (
            <InlinePlanArtifact threadId={threadId} />
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
        </ConversationContent>
        <ConversationScrollButton
          aria-label="Scroll to bottom"
          className="dropdown-glass z-30 size-8 border-0 text-muted-foreground hover:text-foreground"
        >
          <ChevronDown className="size-3.5" />
        </ConversationScrollButton>
      </Conversation>
    </TooltipProvider>
  )
})
