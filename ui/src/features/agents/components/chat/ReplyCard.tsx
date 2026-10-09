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

function headerLabel(
  isLinear: boolean,
  status: ToolExecutionChunk["status"]
): string {
  const pending = status === "in_progress" || status === "pending"
  if (isLinear) return pending ? "Commenting on Linear…" : "Commented on Linear"
  return pending ? "Sending to Slack…" : "Sent to Slack"
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

export function replyBody(chunk: ToolExecutionChunk): string {
  const body =
    chunk.input?.[chunk.toolKind === "linear" ? "comment_body" : "message"]
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
  const isLinear = chunk.toolKind === "linear"
  return (
    <div className="min-w-0 px-space-1 py-0.5">
      <div className="mb-space-1 flex items-center gap-1.5 text-xxs text-secondary">
        {isLinear ? (
          <ChatCircleIcon size={12} weight="regular" aria-hidden />
        ) : (
          <SlackIcon />
        )}
        <span>{headerLabel(isLinear, chunk.status)}</span>
      </div>
      {isLinear ? (
        <Markdown content={replyBody(chunk)} />
      ) : (
        <SlackReply chunk={chunk} />
      )}
    </div>
  )
})
