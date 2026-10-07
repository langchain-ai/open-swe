import { memo } from "react"
import { SlackMrkdwn } from "../messages/SlackMrkdwn"
import { Markdown } from "./Markdown"
import type { ReactNode } from "react"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { ProviderMark } from "@langchain/gtm-platform-design-system/patterns/provider-mark"
import { Receipt } from "@langchain/gtm-platform-design-system/patterns/receipt"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { buttonVariants } from "@langchain/gtm-platform-design-system/ui/button"
import { Separator } from "@langchain/gtm-platform-design-system/ui/separator"

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

/** Slack buttons are drawn as the outline controls they become, but stay inert here. */
const SLACK_BUTTON_CLASS = buttonVariants({
  variant: "outline",
  size: "compact",
})

const SLACK_TEXT_CLASS = "wrap-anywhere whitespace-pre-wrap"

function renderSlackBlocks(blocks: Array<SlackBlock>): ReactNode {
  return (
    <Stack gap="sm">
      {blocks.map((block, index) => {
        if (
          (block.type === "section" || block.type === "context") &&
          isSlackTextObject(block.text)
        ) {
          return (
            <div key={index} className={SLACK_TEXT_CLASS}>
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
            <Inline key={index} gap="sm" wrap>
              {block.elements.map((element, elementIndex) => {
                const label = isSlackTextObject(element.text)
                  ? element.text.text
                  : element.type || "Action"
                return (
                  <span
                    key={elementIndex}
                    className={`${SLACK_BUTTON_CLASS} pointer-events-none`}
                  >
                    {label}
                  </span>
                )
              })}
            </Inline>
          )
        }
        if (block.type === "divider") {
          return <Separator key={index} />
        }
        return null
      })}
    </Stack>
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
    <Receipt
      title={headerLabel(isLinear, chunk.status)}
      titleClassName="text-label"
      spacing="sm"
      mark={<ProviderMark provider={isLinear ? "linear" : "slack"} />}
    >
      {body ? (
        <Box className="max-h-64 overflow-auto px-1 text-body text-ink">
          {isLinear ? (
            <Markdown content={body} />
          ) : blocks ? (
            renderSlackBlocks(blocks)
          ) : (
            <div className={SLACK_TEXT_CLASS}>
              <SlackMrkdwn text={body} />
            </div>
          )}
        </Box>
      ) : undefined}
    </Receipt>
  )
})
