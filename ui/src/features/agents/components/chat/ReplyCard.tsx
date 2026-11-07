import { Button } from "@langchain/macaw-components/Button"
import { Link } from "@langchain/macaw-components/Link"
import { createContext, memo, useContext, useState } from "react"
import { toast } from "sonner"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { SlackMrkdwn, useSlackMarkdown } from "../messages/SlackMrkdwn"
import { Markdown } from "./Markdown"
import type { ReactNode } from "react"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"

interface ReplyCardProps {
  chunk: ToolExecutionChunk
}

type Delivery = "pending" | "failed" | "delivered"

// Reply tools report some failures as a `{"success": false}` result rather than raising.
// A result that carries images arrives as content blocks, with that object as the first text.
function replyResult(
  output: string | undefined
): Record<string, unknown> | undefined {
  if (!output) return undefined
  try {
    const parsed: unknown = JSON.parse(output)
    if (Array.isArray(parsed)) {
      const first: unknown = parsed[0]
      const text =
        !!first && typeof first === "object"
          ? (first as { text?: unknown }).text
          : undefined
      return typeof text === "string" ? replyResult(text) : undefined
    }
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, unknown>)
      : undefined
  } catch {
    return undefined
  }
}

function delivery(chunk: ToolExecutionChunk): Delivery {
  if (chunk.status === "in_progress" || chunk.status === "pending") {
    return "pending"
  }
  if (chunk.status === "error" || replyResult(chunk.output)?.success === false)
    return "failed"
  return "delivered"
}

const HEADER_LABELS: Record<"linear" | "slack", Record<Delivery, string>> = {
  linear: {
    pending: "Commenting on Linear…",
    failed: "Failed to comment on Linear",
    delivered: "Commented on Linear",
  },
  slack: {
    pending: "Sending to Slack…",
    failed: "Failed to send to Slack",
    delivered: "Sent to Slack",
  },
}

type SlackTextObject = { type?: string; text?: string }
type SlackElement = { type?: string; text?: SlackTextObject; value?: string }
type SlackBlock = {
  type?: string
  text?: SlackTextObject
  elements?: Array<SlackElement>
}
type SlackButton = { label: string; answer?: string }

/** Sends a reply option as the user's next message, as a Slack click does. */
export const ReplyOptionContext = createContext<
  ((answer: string) => Promise<void>) | undefined
>(undefined)

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

function optionAnswer(value: string | undefined): string | undefined {
  if (!value) return undefined
  try {
    const parsed: unknown = JSON.parse(value)
    if (!parsed || typeof parsed !== "object") return undefined
    const { type, response } = parsed as { type?: unknown; response?: unknown }
    return type === "open_swe_option" && typeof response === "string"
      ? response
      : undefined
  } catch {
    return undefined
  }
}

function elementButton(element: SlackElement): SlackButton {
  return {
    label: isSlackTextObject(element.text)
      ? (element.text.text ?? "")
      : element.type || "Action",
    answer: optionAnswer(element.value),
  }
}

const buttonClass =
  "rounded-md border border-default bg-surface-level-1 px-space-2 py-space-1 text-xxs text-primary"

function SlackButtons({ buttons }: { buttons: Array<SlackButton> }) {
  const onAnswer = useContext(ReplyOptionContext)
  const [sending, setSending] = useState(false)
  const choose = (answer: string) => {
    if (!onAnswer) return
    setSending(true)
    onAnswer(answer)
      .catch(() => toast.error("Could not send your answer"))
      .finally(() => setSending(false))
  }
  return (
    <div className="flex flex-wrap gap-space-2">
      {buttons.map(({ label, answer }, index) =>
        onAnswer && answer !== undefined ? (
          <Button
            color="secondary"
            variant="plain"
            size="sm"
            aria-label={label}
            key={index}
            type="button"
            disabled={sending}
            onClick={() => choose(answer)}
            className={`${buttonClass} cursor-pointer hover:bg-surface-level-2 disabled:cursor-default disabled:opacity-60`}
          >
            {label}
          </Button>
        ) : (
          <span key={index} className={buttonClass}>
            {label}
          </span>
        )
      )}
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
              buttons={block.elements.map(elementButton)}
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
      {options.length > 0 && (
        <SlackButtons
          buttons={options.map((option) => ({
            label: option,
            answer: option.trim(),
          }))}
        />
      )}
    </div>
  )
}

export const ReplyCard = memo(function ReplyCard({ chunk }: ReplyCardProps) {
  const isLinear = chunk.toolKind === "linear"
  const state = delivery(chunk)
  const messageUrl =
    !isLinear && state === "delivered"
      ? replyResult(chunk.output)?.message_url
      : undefined
  const destination =
    typeof messageUrl === "string" && URL.canParse(messageUrl)
      ? new URL(messageUrl)
      : undefined
  const slackUrl =
    destination?.protocol === "https:" &&
    (destination.hostname === "slack.com" ||
      destination.hostname.endsWith(".slack.com")) &&
    /^\/archives\/[A-Z0-9]+\/p\d+$/.test(destination.pathname)
      ? destination.href
      : undefined
  const label = HEADER_LABELS[isLinear ? "linear" : "slack"][state]
  return (
    <div className="min-w-0 px-space-1 py-0.5">
      <div className="mb-space-1 flex items-center gap-space-2 text-xxs text-secondary">
        {isLinear ? (
          <ChatCircleIcon size={12} weight="regular" aria-hidden />
        ) : !slackUrl ? (
          <SlackLogoIcon size={12} weight="fill" aria-hidden />
        ) : null}
        {slackUrl ? (
          <Link
            href={slackUrl}
            variant="xs"
            target="_blank"
            rel="noopener noreferrer"
            leftDecorator={SlackLogoIcon}
            iconWeight="fill"
            className="gap-space-2"
          >
            {label}
          </Link>
        ) : (
          <span
            className={state === "failed" ? "text-error-secondary" : undefined}
          >
            {label}
          </span>
        )}
      </div>
      {isLinear ? (
        <Markdown content={replyBody(chunk)} />
      ) : (
        <SlackReply chunk={chunk} />
      )}
    </div>
  )
})
