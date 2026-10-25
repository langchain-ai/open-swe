import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"
import { useEffect, useRef, useState } from "react"

import { SkillPromptText } from "../SkillBadge"
import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"
import { parseMessageSegments } from "@/features/agents/utils/quoteReply"
import { MessageImage } from "./MessageImage"
import { MessageTimestamp } from "./MessageTimestamp"
import { ExpandableMessageChip } from "./ExpandableMessageChip"
import { TaskEventMessage } from "./TaskEventMessage"
import { SlackMrkdwn } from "./SlackMrkdwn"
import type { Message } from "@/features/agents/lib/types"
import { cn } from "@/lib/utils"

const COLLAPSED_MAX_HEIGHT_PX = 250

export function UserMessage({ message }: { message: Message }) {
  if (message.taskEvent) {
    return (
      <TaskEventMessage
        event={message.taskEvent}
        messageId={message.id}
        timestamp={message.timestampIsFallback ? undefined : message.timestamp}
      />
    )
  }
  return <StandardUserMessage message={message} />
}

function StandardUserMessage({ message }: { message: Message }) {
  const isSystem = message.structuredSenderKind === "system"
  const isSlack = message.structuredSurface === "slack"
  const content = message.chunks
    .filter((c) => c.kind === "text")
    .map((c) => c.text)
    .join("")
  const segments = parseMessageSegments(content)

  const images = message.chunks.filter((c) => c.kind === "image")
  const hasBody = segments.length > 0 || images.length > 0
  const [expanded, setExpanded] = useState(false)
  const [isTruncated, setIsTruncated] = useState(false)
  const textRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = textRef.current
    if (!el) return
    const measure = () =>
      setIsTruncated(el.scrollHeight > COLLAPSED_MAX_HEIGHT_PX + 1)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [content])

  const body = (
    <>
      {hasBody && (
        <div
          className={cn(
            "relative max-w-full overflow-hidden rounded-xl p-space-3",
            isSystem
              ? "mt-space-1 border border-default bg-surface-level-2"
              : "bg-surface-level-2"
          )}
        >
          {images.length > 0 && (
            <div className="mb-space-2 grid max-w-[420px] grid-cols-2 gap-space-2">
              {images.map((img, i) => (
                <div
                  key={i}
                  className="overflow-hidden rounded-lg border border-default bg-surface-level-1"
                >
                  <MessageImage
                    chunk={img}
                    className="block h-auto max-h-[220px] w-full object-cover"
                  />
                </div>
              ))}
            </div>
          )}
          {segments.length > 0 && (
            <div
              ref={textRef}
              className={cn(
                "flex flex-col gap-space-2 text-sm leading-[1.6] [overflow-wrap:anywhere] text-primary",
                !isSystem && !expanded && "overflow-hidden"
              )}
              style={
                !isSystem && !expanded
                  ? { maxHeight: COLLAPSED_MAX_HEIGHT_PX }
                  : undefined
              }
            >
              {segments.map((segment, i) =>
                segment.kind === "code" ? (
                  <CodeBlock
                    key={i}
                    title={segment.excerpt.location}
                    language={segment.excerpt.language}
                    text={segment.excerpt.code}
                  />
                ) : segment.kind === "quote" ? (
                  <blockquote
                    key={i}
                    className="border-l-2 border-default pl-space-3 whitespace-pre-wrap text-secondary"
                  >
                    {segment.text}
                  </blockquote>
                ) : (
                  <div key={i} className="whitespace-pre-wrap">
                    {isSlack ? (
                      <SlackMrkdwn text={segment.text} />
                    ) : (
                      <SkillPromptText text={segment.text} />
                    )}
                  </div>
                )
              )}
            </div>
          )}
          {!isSystem && isTruncated && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              data-testid="user-message-show-more"
              className="mt-space-1 rounded-sm text-xs text-secondary transition-colors duration-normal hover:text-primary focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
            >
              {expanded ? "Show less" : "Show more"}
            </button>
          )}
        </div>
      )}
      {!message.timestampIsFallback && (
        <MessageTimestamp
          timestamp={message.timestamp}
          align={isSystem ? "left" : "right"}
          className="mt-space-1 pr-space-1"
        />
      )}
    </>
  )

  return (
    <div
      className={cn(
        "group/turn my-space-4 flex flex-col gap-space-1",
        isSystem ? "items-start" : "items-end"
      )}
      data-testid="user-message"
      data-message-id={message.id}
      data-message-delivery-status={message.deliveryStatus}
      data-message-sender-kind={message.structuredSenderKind}
      data-message-surface={message.structuredSurface}
    >
      <div
        className={cn(
          "flex max-w-[80%] flex-col",
          isSystem ? "items-start" : "items-end"
        )}
      >
        {!isSystem &&
          (message.structuredSenderName ||
            isSlack ||
            message.structuredSenderIsBot) && (
            <div className="mb-space-1 flex items-center gap-space-1 px-space-1 text-xxs font-medium text-secondary">
              {isSlack && (
                <SlackLogoIcon
                  size={12}
                  weight="fill"
                  role="img"
                  aria-label="Slack"
                />
              )}
              {message.structuredSenderIsBot && (
                <RobotIcon
                  size={12}
                  weight="regular"
                  role="img"
                  aria-label="Bot"
                  data-testid="user-message-bot-icon"
                />
              )}
              {message.structuredSenderName && (
                <span>{message.structuredSenderName}</span>
              )}
              {message.structuredSenderNote && (
                <span className="font-normal text-tertiary">
                  {" · "}
                  {message.structuredSenderNote}
                </span>
              )}
            </div>
          )}
        {isSystem ? (
          <ExpandableMessageChip
            testId="system-message-toggle"
            label={
              <>
                <span>{message.structuredSenderName || "Context"}</span>
                {message.structuredSenderNote && (
                  <span className="text-tertiary">
                    · {message.structuredSenderNote}
                  </span>
                )}
              </>
            }
          >
            {body}
          </ExpandableMessageChip>
        ) : (
          body
        )}
        {message.deliveryStatus && (
          <div
            className={cn(
              "mt-space-1 pr-space-1 text-right text-xxs leading-4",
              message.deliveryStatus === "failed"
                ? "text-error-secondary"
                : "text-secondary"
            )}
          >
            {message.deliveryStatus !== "failed" ? (
              "Sending"
            ) : (
              <span
                title={message.deliveryError}
                data-testid="user-message-delivery-error"
              >
                {message.deliveryError
                  ? `Failed to send · ${message.deliveryError}`
                  : "Failed to send"}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
