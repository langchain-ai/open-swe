import { memo } from "react"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { SlackMrkdwn, useSlackMarkdown } from "../messages/SlackMrkdwn"
import { Markdown } from "./Markdown"
import type { ReactNode } from "react"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"

interface ReplyCardProps {
  chunk: ToolExecutionChunk
}

function SlackIcon() {
  return (
    <svg viewBox="0 0 122.8 122.8" width={12} height={12} aria-hidden>
      <path
        fill="#e01e5a"
        d="M25.8 77.6a12.9 12.9 0 1 1-12.9-12.9h12.9zm6.5 0a12.9 12.9 0 0 1 25.8 0v32.3a12.9 12.9 0 0 1-25.8 0z"
      />
      <path
        fill="#36c5f0"
        d="M45.2 25.8a12.9 12.9 0 1 1 12.9-12.9v12.9zm0 6.5a12.9 12.9 0 0 1 0 25.8H12.9a12.9 12.9 0 0 1 0-25.8z"
      />
      <path
        fill="#2eb67d"
        d="M97 45.2a12.9 12.9 0 1 1 12.9 12.9H97zm-6.5 0a12.9 12.9 0 0 1-25.8 0V12.9a12.9 12.9 0 0 1 25.8 0z"
      />
      <path
        fill="#ecb22e"
        d="M77.6 97a12.9 12.9 0 1 1-12.9 12.9V97zm0-6.5a12.9 12.9 0 0 1 0-25.8h32.3a12.9 12.9 0 0 1 0 25.8z"
      />
    </svg>
  )
}

type Delivery = "pending" | "failed" | "delivered"

// Reply tools report some failures as a `{"success": false}` result rather than raising.
// A result that carries images arrives as content blocks, with that object as the first text.
function reportsFailure(output: string | undefined): boolean {
  if (!output) return false
  try {
    const parsed: unknown = JSON.parse(output)
    if (Array.isArray(parsed)) {
      const first: unknown = parsed[0]
      const text =
        !!first && typeof first === "object"
          ? (first as { text?: unknown }).text
          : undefined
      return typeof text === "string" && reportsFailure(text)
    }
    return (
      !!parsed &&
      typeof parsed === "object" &&
      (parsed as { success?: unknown }).success === false
    )
  } catch {
    return false
  }
}

function delivery(chunk: ToolExecutionChunk): Delivery {
  if (chunk.status === "in_progress" || chunk.status === "pending") {
    return "pending"
  }
  if (chunk.status === "error" || reportsFailure(chunk.output)) return "failed"
  return "delivered"
}

type ReplyKind = "linear" | "notion" | "slack"

function replyKind(chunk: ToolExecutionChunk): ReplyKind {
  if (chunk.toolKind === "linear" || chunk.toolKind === "notion") {
    return chunk.toolKind
  }
  return "slack"
}

const HEADER_LABELS: Record<ReplyKind, Record<Delivery, string>> = {
  linear: {
    pending: "Commenting on Linear…",
    failed: "Failed to comment on Linear",
    delivered: "Commented on Linear",
  },
  notion: {
    pending: "Commenting on Notion…",
    failed: "Failed to comment on Notion",
    delivered: "Commented on Notion",
  },
  slack: {
    pending: "Sending to Slack…",
    failed: "Failed to send to Slack",
    delivered: "Sent to Slack",
  },
}

type SlackTextObject = { type?: string; text?: string }
type SlackBlock = {
  type?: string
  text?: SlackTextObject
  elements?: Array<{ type?: string; text?: SlackTextObject }>
}

function isSlackTextObject(value: unknown): value is SlackTextObject {
  return (
    !!value &&
    typeof value === "object" &&
    typeof (value as SlackTextObject).text === "string"
  )
}

function isSlackBlockArray(value: unknown): value is Array<SlackBlock> {
  return (
    Array.isArray(value) &&
    value.every((block) => !!block && typeof block === "object")
  )
}

function SlackButtons({ labels }: { labels: Array<string> }) {
  return (
    <div className="flex flex-wrap gap-space-2">
      {labels.map((label, index) => (
        <span
          key={index}
          className="rounded-md border border-default bg-surface-level-1 px-space-2 py-space-1 text-xxs text-primary"
        >
          {label}
        </span>
      ))}
    </div>
  )
}

function renderSlackBlocks(blocks: Array<SlackBlock>): ReactNode {
  return (
    <div className="flex flex-col gap-space-2">
      {blocks.map((block, index) => {
        if (
          (block.type === "section" || block.type === "context") &&
          isSlackTextObject(block.text)
        ) {
          return (
            <div
              key={index}
              className="[overflow-wrap:anywhere] break-words whitespace-pre-wrap"
            >
              {block.text.type === "mrkdwn" ? (
                <SlackMrkdwn text={block.text.text ?? ""} />
              ) : (
                block.text.text
              )}
            </div>
          )
        }
        if (block.type === "actions" && Array.isArray(block.elements)) {
          return (
            <SlackButtons
              key={index}
              labels={block.elements.map((element) =>
                isSlackTextObject(element.text)
                  ? (element.text.text ?? "")
                  : element.type || "Action"
              )}
            />
          )
        }
        if (block.type === "divider") {
          return <div key={index} className="border-t border-subtle" />
        }
        return null
      })}
    </div>
  )
}

function optionLabels(options: unknown): Array<string> {
  if (!Array.isArray(options)) return []
  return options
    .filter((option): option is string => typeof option === "string")
    .filter((option) => option.trim())
    .slice(0, 5)
}

const BODY_FIELDS: Record<ReplyKind, string> = {
  linear: "comment_body",
  notion: "text",
  slack: "message",
}

export function replyBody(chunk: ToolExecutionChunk): string {
  const body = chunk.input?.[BODY_FIELDS[replyKind(chunk)]]
  return typeof body === "string" ? body : ""
}

function SlackReply({ chunk }: ReplyCardProps) {
  const markdown = useSlackMarkdown(replyBody(chunk))
  if (isSlackBlockArray(chunk.input?.blocks)) {
    return renderSlackBlocks(chunk.input.blocks)
  }
  const options = optionLabels(chunk.input?.options)
  return (
    <div className="flex flex-col gap-space-2">
      <Markdown content={markdown} />
      {options.length > 0 && <SlackButtons labels={options} />}
    </div>
  )
}

export const ReplyCard = memo(function ReplyCard({ chunk }: ReplyCardProps) {
  const kind = replyKind(chunk)
  const state = delivery(chunk)
  return (
    <div className="min-w-0 px-space-1 py-0.5">
      <div className="mb-space-1 flex items-center gap-space-2 text-xxs text-secondary">
        {kind === "slack" ? (
          <SlackIcon />
        ) : (
          <ChatCircleIcon size={12} weight="regular" aria-hidden />
        )}
        <span
          className={state === "failed" ? "text-error-secondary" : undefined}
        >
          {HEADER_LABELS[kind][state]}
        </span>
      </div>
      {kind === "slack" ? (
        <SlackReply chunk={chunk} />
      ) : (
        <Markdown content={replyBody(chunk)} />
      )}
    </div>
  )
})
