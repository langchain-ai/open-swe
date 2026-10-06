import { useState } from "react"

import { SkillPromptText } from "../SkillBadge"
import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"
import { parseExcerpts } from "@/features/agents/utils/codeExcerpt"
import { MessageImage } from "./MessageImage"
import { MessageTimestamp } from "./MessageTimestamp"
import { SlackMrkdwn } from "./SlackMrkdwn"
import type { AnyImageChunk, Message } from "@/features/agents/lib/types"
import {
  TurnCopyButton,
  UserTurn,
  UserTurnCopy,
} from "@langchain/gtm-platform-design-system/patterns/agent-thread"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@langchain/gtm-platform-design-system/ui/collapsible"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ProviderLogo } from "@langchain/gtm-platform-design-system/ui/provider-logos"
import { Bot } from "@/components/glyphs"
import { cn } from "@/lib/utils"

/** Excerpts, images and prose: the turn's content, shared by both shapes. */
function MessageBody({
  excerpts,
  images,
  text,
  isSlack,
}: {
  excerpts: ReturnType<typeof parseExcerpts>["excerpts"]
  images: Array<AnyImageChunk>
  text: string
  isSlack: boolean
}) {
  return (
    <Stack gap="sm" className="min-w-0">
      {excerpts.map((excerpt, i) => (
        <CodeBlock
          key={i}
          title={excerpt.location}
          language={excerpt.language}
          text={excerpt.code}
        />
      ))}
      {images.length > 0 && (
        <Box className="grid grid-cols-2 gap-2">
          {images.map((img, i) => (
            <Box
              key={i}
              bg="canvas"
              border="line"
              radius="compact"
              className="overflow-hidden"
            >
              <MessageImage
                chunk={img}
                className="block h-auto max-h-56 w-full object-cover"
              />
            </Box>
          ))}
        </Box>
      )}
      {text && (
        <UserTurnCopy text={text}>
          {isSlack ? <SlackMrkdwn text={text} /> : <SkillPromptText text={text} />}
        </UserTurnCopy>
      )}
    </Stack>
  )
}

function SenderLine({ message }: { message: Message }) {
  const isSlack = message.structuredSurface === "slack"
  if (
    !message.structuredSenderName &&
    !isSlack &&
    !message.structuredSenderIsBot
  ) {
    return null
  }
  return (
    <Inline
      gap="xs"
      align="center"
      className="px-1 text-meta font-medium text-ink-subtle"
    >
      {isSlack && (
        <ProviderLogo provider="slack" title="Slack" className="size-3.5" />
      )}
      {message.structuredSenderIsBot && (
        <Box
          render={<span />}
          data-testid="user-message-bot-icon"
          className="inline-flex"
        >
          <Icon icon={Bot} size="sm" label="Bot" />
        </Box>
      )}
      {message.structuredSenderName && (
        <span>{message.structuredSenderName}</span>
      )}
      {message.structuredSenderNote && (
        <span className="font-normal">
          {" · "}
          {message.structuredSenderNote}
        </span>
      )}
    </Inline>
  )
}

export function UserMessage({ message }: { message: Message }) {
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
  const body = (
    <MessageBody
      excerpts={excerpts}
      images={images}
      text={text}
      isSlack={isSlack}
    />
  )
  const timestamp = message.timestampIsFallback ? null : (
    <MessageTimestamp
      timestamp={message.timestamp}
      align={isSystem ? "left" : "right"}
    />
  )

  return (
    <Stack
      gap="xs"
      align={isSystem ? "start" : "end"}
      className="group/turn w-full min-w-0"
      data-testid="user-message"
      data-message-id={message.id}
      data-message-delivery-status={message.deliveryStatus}
      data-message-sender-kind={message.structuredSenderKind}
      data-message-surface={message.structuredSurface}
    >
      {isSystem ? (
        <Collapsible
          open={expanded}
          onOpenChange={setExpanded}
          className="flex w-full max-w-lg flex-col items-start"
        >
          <CollapsibleTrigger
            data-testid="system-message-toggle"
            className="h-control-sm cursor-pointer gap-1 rounded-compact border border-line bg-muted px-2.5 text-meta text-ink-subtle transition-colors duration-fast ease-out-quint hover:bg-hover hover:text-ink motion-reduce:transition-none"
          >
            <CollapsibleChevron />
            <span>{message.structuredSenderName || "Context"}</span>
            {message.structuredSenderNote && (
              <span>· {message.structuredSenderNote}</span>
            )}
          </CollapsibleTrigger>
          <CollapsibleContent className="w-full">
            <Stack gap="xs" className="pt-1">
              {hasBody && (
                <Box
                  bg="muted"
                  border="line"
                  radius="panel"
                  className="px-3 py-2.5 text-body text-ink"
                >
                  {body}
                </Box>
              )}
              {timestamp}
            </Stack>
          </CollapsibleContent>
        </Collapsible>
      ) : (
        <>
          <SenderLine message={message} />
          {hasBody && <UserTurn>{body}</UserTurn>}
          <Inline gap="sm" align="center" justify="end" className="min-h-5">
            {message.deliveryStatus && (
              <Box
                render={<span />}
                className={cn(
                  "text-meta",
                  message.deliveryStatus === "failed"
                    ? "text-risk"
                    : "text-ink-subtle"
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
              </Box>
            )}
            {timestamp}
            {text && <TurnCopyButton text={text} />}
          </Inline>
        </>
      )}
    </Stack>
  )
}
