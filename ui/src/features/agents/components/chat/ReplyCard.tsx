import { memo } from "react"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { SlackMrkdwn } from "../messages/SlackMrkdwn"
import { Markdown } from "./Markdown"
import type { ReactNode } from "react"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"

interface ReplyCardProps {
  chunk: ToolExecutionChunk
}

function headerLabel(
  isLinear: boolean,
  status: ToolExecutionChunk["status"]
): string {
  const pending = status === "in_progress" || status === "pending"
  if (isLinear) return pending ? "Commenting on Linear…" : "Commented on Linear"
  return pending ? "Replying in Slack…" : "Replied in Slack"
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

function blocksFromOptions(
  message: string,
  options: unknown
): Array<SlackBlock> | null {
  if (!Array.isArray(options)) return null
  const cleanOptions = options.filter(
    (option): option is string =>
      typeof option === "string" && option.trim().length > 0
  )
  if (cleanOptions.length === 0) return null
  return [
    { type: "section", text: { type: "mrkdwn", text: message } },
    {
      type: "actions",
      elements: cleanOptions.slice(0, 5).map((option) => ({
        type: "button",
        text: { type: "plain_text", text: option },
      })),
    },
  ]
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
            <div key={index} className="flex flex-wrap gap-space-2">
              {block.elements.map((element, elementIndex) => {
                const label = isSlackTextObject(element.text)
                  ? element.text.text
                  : element.type || "Action"
                return (
                  <span
                    key={elementIndex}
                    className="rounded-md border border-default bg-surface-level-1 px-space-2 py-space-1 text-xxs text-primary"
                  >
                    {label}
                  </span>
                )
              })}
            </div>
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

export const ReplyCard = memo(function ReplyCard({ chunk }: ReplyCardProps) {
  const isLinear = chunk.toolKind === "linear"
  const body =
    ((isLinear ? chunk.input?.comment_body : chunk.input?.message) as string) ||
    ""
  const blocks = !isLinear
    ? isSlackBlockArray(chunk.input?.blocks)
      ? chunk.input.blocks
      : blocksFromOptions(body, chunk.input?.options)
    : null

  return (
    <div className="my-1">
      <div className="flex items-center gap-1.5 py-space-1 text-xxs text-secondary">
        <ChatCircleIcon
          size={14}
          weight="regular"
          className="shrink-0 text-icon-tertiary"
          aria-hidden
        />
        <span>{headerLabel(isLinear, chunk.status)}</span>
      </div>
      {body && (
        <div className="overflow-hidden rounded-xl border border-subtle bg-surface-level-2">
          <div className="max-h-[250px] overflow-auto px-space-3 py-space-2 text-sm text-primary">
            {isLinear ? (
              <Markdown content={body} />
            ) : blocks ? (
              renderSlackBlocks(blocks)
            ) : (
              <div className="[overflow-wrap:anywhere] break-words whitespace-pre-wrap">
                <SlackMrkdwn text={body} />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
})
