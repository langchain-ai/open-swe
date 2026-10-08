import { Bot } from "lucide-react"
import { IoLogoSlack } from "react-icons/io5"
import { useEffect, useRef, useState } from "react"

import { SkillPromptText } from "../SkillBadge"
import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"
import { parseExcerpts } from "@/features/agents/utils/codeExcerpt"
import { MessageImage } from "./MessageImage"
import { MessageTimestamp } from "./MessageTimestamp"
import { ExpandableMessageChip } from "./ExpandableMessageChip"
import { TaskEventMessage } from "./TaskEventMessage"
import { SlackMrkdwn } from "./SlackMrkdwn"
import type { Message } from "@/features/agents/lib/types"

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
  const { excerpts, text } = parseExcerpts(
    message.chunks
      .filter((c) => c.kind === "text")
      .map((c) => c.text)
      .join("")
  )

  const images = message.chunks.filter((c) => c.kind === "image")
  const hasBody = Boolean(text) || images.length > 0 || excerpts.length > 0
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
  }, [text])

  const body = (
    <>
      {hasBody && (
        <div
          className={`relative overflow-hidden rounded-2xl p-3 ${
            isSystem ? "mt-1 border border-default bg-surface-level-2/50" : "bg-surface-level-1-hover"
          }`}
        >
          {excerpts.map((excerpt, i) => (
            <CodeBlock
              key={i}
              title={excerpt.location}
              language={excerpt.language}
              text={excerpt.code}
            />
          ))}
          {images.length > 0 && (
            <div className="mb-2 grid max-w-[420px] grid-cols-2 gap-2">
              {images.map((img, i) => (
                <div
                  key={i}
                  className="overflow-hidden rounded-lg border border-default bg-surface-level-1/70"
                >
                  <MessageImage
                    chunk={img}
                    className="block h-auto max-h-[220px] w-full object-cover"
                  />
                </div>
              ))}
            </div>
          )}
          {text && (
            <div
              ref={textRef}
              className={`text-[14px] leading-[1.6] break-words whitespace-pre-wrap text-primary ${
                !isSystem && !expanded ? "overflow-hidden" : ""
              }`}
              style={
                !isSystem && !expanded
                  ? { maxHeight: COLLAPSED_MAX_HEIGHT_PX }
                  : undefined
              }
            >
              {isSlack ? (
                <SlackMrkdwn text={text} />
              ) : (
                <SkillPromptText text={text} />
              )}
            </div>
          )}
          {!isSystem && isTruncated && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              data-testid="user-message-show-more"
              className="mt-1 text-[13px] text-secondary transition-colors hover:text-primary"
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
          className="mt-1 pr-1"
        />
      )}
    </>
  )

  return (
    <div
      className={`group/turn my-4 flex flex-col gap-1 ${isSystem ? "items-start" : "items-end"}`}
      data-testid="user-message"
      data-message-id={message.id}
      data-message-delivery-status={message.deliveryStatus}
      data-message-sender-kind={message.structuredSenderKind}
      data-message-surface={message.structuredSurface}
    >
      <div className="max-w-[80%]">
        {!isSystem &&
          (message.structuredSenderName ||
            isSlack ||
            message.structuredSenderIsBot) && (
            <div className="mb-1 flex items-center gap-1 px-1 text-[11px] font-medium text-secondary">
              {isSlack && (
                <IoLogoSlack className="size-3" role="img" aria-label="Slack" />
              )}
              {message.structuredSenderIsBot && (
                <Bot
                  className="size-3"
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
            className={`mt-1 pr-1 text-right text-[11px] ${
              message.deliveryStatus === "failed"
                ? "text-error-secondary"
                : "text-secondary"
            }`}
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
