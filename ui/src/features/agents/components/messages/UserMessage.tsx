import { ChevronDown, ChevronRight } from "lucide-react"
import { IoLogoSlack } from "react-icons/io5"
import { memo, useCallback, useLayoutEffect, useRef, useState } from "react"

import { SkillPromptText } from "../SkillBadge"
import { MessageTimestamp } from "./MessageTimestamp"
import { SlackMrkdwn } from "./SlackMrkdwn"
import type { Message } from "@/features/agents/lib/types"

export const UserMessage = memo(function UserMessageComponent({
  message,
}: {
  message: Message
}) {
  const isSystem = message.structuredSenderKind === "system"
  const isSlack = message.structuredSurface === "slack"
  const text = message.chunks
    .filter((c) => c.kind === "text")
    .map((c) => c.text)
    .join("")

  const images = message.chunks.filter((c) => c.kind === "image")
  const [expanded, setExpanded] = useState(false)
  const textRef = useRef<HTMLDivElement>(null)
  const [scrolledFromTop, setScrolledFromTop] = useState(false)
  const [scrolledFromBottom, setScrolledFromBottom] = useState(false)

  const updateScrollIndicators = useCallback(() => {
    const el = textRef.current
    if (!el) return
    setScrolledFromTop(el.scrollTop > 0)
    setScrolledFromBottom(el.scrollTop < el.scrollHeight - el.clientHeight - 1)
  }, [])

  useLayoutEffect(() => {
    updateScrollIndicators()
  }, [text, updateScrollIndicators])

  const topStop = scrolledFromTop ? "transparent 0, black 24px" : "black 0"
  const bottomStop = scrolledFromBottom
    ? "black calc(100% - 24px), transparent 100%"
    : "black 100%"
  const textEdgeMask =
    scrolledFromTop || scrolledFromBottom
      ? `linear-gradient(to bottom, ${topStop}, ${bottomStop})`
      : undefined

  return (
    <div
      className={`group/turn my-4 flex flex-col gap-1 [contain-intrinsic-size:auto_100px] [content-visibility:auto] ${isSystem ? "items-start" : "items-end"}`}
      data-testid="user-message"
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
                onScroll={updateScrollIndicators}
                className="max-h-[250px] overflow-auto text-[14px] leading-[1.6] break-words whitespace-pre-wrap text-accent-foreground"
                style={{
                  maskImage: textEdgeMask,
                  WebkitMaskImage: textEdgeMask,
                }}
              >
                {isSlack ? (
                  <SlackMrkdwn text={text} />
                ) : (
                  <SkillPromptText text={text} />
                )}
              </div>
            )}
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
})
