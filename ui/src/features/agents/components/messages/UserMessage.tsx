import { ChevronDown, ChevronRight } from "lucide-react"
import { IoLogoSlack } from "react-icons/io5"
import { useEffect, useRef, useState } from "react"

import { SkillPromptText } from "../SkillBadge"
import { MessageTimestamp } from "./MessageTimestamp"
import { SlackMrkdwn } from "./SlackMrkdwn"
import type { Message } from "@/features/agents/lib/types"

const COLLAPSED_MAX_HEIGHT_PX = 250

export function UserMessage({ message }: { message: Message }) {
  const isSystem = message.structuredSenderKind === "system"
  const isSlack = message.structuredSurface === "slack"
  const text = message.chunks
    .filter((c) => c.kind === "text")
    .map((c) => c.text)
    .join("")

  const images = message.chunks.filter((c) => c.kind === "image")
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
        {isSystem ? (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            data-testid="system-message-toggle"
            className="flex items-center gap-1.5 rounded-full border border-border bg-muted/50 px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-accent/30"
          >
            {expanded ? (
              <ChevronDown className="size-3" />
            ) : (
              <ChevronRight className="size-3" />
            )}
            <span>{message.structuredSenderName || "Context"}</span>
            {message.structuredSenderNote && (
              <span className="text-muted-foreground/70">
                · {message.structuredSenderNote}
              </span>
            )}
          </button>
        ) : (
          (message.structuredSenderName || isSlack) && (
            <div className="mb-1 flex items-center gap-1 px-1 text-[11px] font-medium text-muted-foreground">
              {isSlack && (
                <IoLogoSlack className="size-3" role="img" aria-label="Slack" />
              )}
              {message.structuredSenderName && (
                <span>{message.structuredSenderName}</span>
              )}
              {message.structuredSenderNote && (
                <span className="font-normal text-muted-foreground/70">
                  {" · "}
                  {message.structuredSenderNote}
                </span>
              )}
            </div>
          )
        )}
        {(!isSystem || expanded) && (text || images.length > 0) && (
          <div
            className={`relative overflow-hidden rounded-2xl p-3 ${
              isSystem ? "mt-1 border border-border bg-muted/50" : "bg-accent"
            }`}
          >
            {images.length > 0 && (
              <div className="mb-2 grid max-w-[420px] grid-cols-2 gap-2">
                {images.map((img, i) => (
                  <div
                    key={i}
                    className="overflow-hidden rounded-lg border border-border/80 bg-background/70"
                  >
                    <img
                      src={`data:${img.mimeType};base64,${img.base64}`}
                      alt={img.fileName || "image"}
                      className="block h-auto max-h-[220px] w-full object-cover"
                    />
                  </div>
                ))}
              </div>
            )}
            {text && (
              <div
                ref={textRef}
                className={`text-[14px] leading-[1.6] break-words whitespace-pre-wrap text-accent-foreground ${
                  !expanded ? "overflow-hidden" : ""
                }`}
                style={
                  !expanded ? { maxHeight: COLLAPSED_MAX_HEIGHT_PX } : undefined
                }
              >
                {isSlack ? (
                  <SlackMrkdwn text={text} />
                ) : (
                  <SkillPromptText text={text} />
                )}
              </div>
            )}
            {isTruncated && (
              <button
                type="button"
                onClick={() => setExpanded((value) => !value)}
                aria-expanded={expanded}
                data-testid="user-message-show-more"
                className="mt-1 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
              >
                {expanded ? "Show less" : "Show more"}
              </button>
            )}
          </div>
        )}
        {message.deliveryStatus && (
          <div
            className={`mt-1 pr-1 text-right text-[11px] ${
              message.deliveryStatus === "failed"
                ? "text-destructive"
                : "text-muted-foreground"
            }`}
          >
            {message.deliveryStatus === "failed" ? "Failed to send" : "Sending"}
          </div>
        )}
        {!message.timestampIsFallback && (!isSystem || expanded) && (
          <MessageTimestamp
            timestamp={message.timestamp}
            align={isSystem ? "left" : "right"}
            className="mt-1 pr-1"
          />
        )}
      </div>
    </div>
  )
}
