import { memo } from "react"
import { MessageCircle } from "lucide-react"
import { Markdown } from "./Markdown"
import type { ReactNode } from "react"
import type { ToolExecutionChunk } from "@/lib/agents/types"

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

const SLACK_TOKEN = /<([^>]+)>/g
const LINK_CLASS =
  "text-[color:var(--ui-accent)] underline decoration-[color:var(--ui-accent)]/50 break-words [overflow-wrap:anywhere]"

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

function isSlackBlockArray(value: unknown): value is SlackBlock[] {
  return (
    Array.isArray(value) &&
    value.every((block) => !!block && typeof block === "object")
  )
}

// Slack mrkdwn isn't standard Markdown — rewrite its link/mention syntax for display
// rather than feeding it to the Markdown renderer (which mis-renders *bold* etc.).
function renderSlackBody(text: string): Array<ReactNode> {
  const nodes: Array<ReactNode> = []
  let lastIndex = 0
  let key = 0
  SLACK_TOKEN.lastIndex = 0
  for (
    let match = SLACK_TOKEN.exec(text);
    match;
    match = SLACK_TOKEN.exec(text)
  ) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index))
    const token = match[1] ?? ""
    const sep = token.indexOf("|")
    const target = sep === -1 ? token : token.slice(0, sep)
    const label = sep === -1 ? "" : token.slice(sep + 1)
    if (target.startsWith("@") || target.startsWith("#")) {
      const sigil = target[0]
      nodes.push(
        <span key={key++} className="text-[color:var(--ui-accent)]">
          {sigil}
          {label || target.slice(1)}
        </span>
      )
    } else if (target.startsWith("!")) {
      nodes.push(
        <span key={key++} className="text-[color:var(--ui-accent)]">
          @{label || target.slice(1)}
        </span>
      )
    } else {
      nodes.push(
        <a
          key={key++}
          href={target}
          target="_blank"
          rel="noreferrer"
          className={LINK_CLASS}
        >
          {label || target}
        </a>
      )
    }
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return nodes
}

function blocksFromOptions(
  message: string,
  options: unknown
): SlackBlock[] | null {
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

function renderSlackBlocks(blocks: SlackBlock[]): ReactNode {
  return (
    <div className="flex flex-col gap-2">
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
              {renderSlackBody(block.text.text ?? "")}
            </div>
          )
        }
        if (block.type === "actions" && Array.isArray(block.elements)) {
          return (
            <div key={index} className="flex flex-wrap gap-2">
              {block.elements.map((element, elementIndex) => {
                const label = isSlackTextObject(element.text)
                  ? element.text.text
                  : element.type || "Action"
                return (
                  <span
                    key={elementIndex}
                    className="rounded-md border border-[var(--ui-border)] bg-[var(--ui-panel)] px-2 py-1 text-[12px] text-[color:var(--ui-text)]"
                  >
                    {label}
                  </span>
                )
              })}
            </div>
          )
        }
        if (block.type === "divider") {
          return (
            <div
              key={index}
              className="border-t border-[var(--ui-border-subtle)]"
            />
          )
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
      <div className="flex items-center gap-1.5 py-1 text-[12px] text-[color:var(--ui-text-muted)]">
        <MessageCircle className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span>{headerLabel(isLinear, chunk.status)}</span>
      </div>
      {body && (
        <div className="overflow-hidden rounded-xl border border-[var(--ui-border-subtle)] bg-[var(--ui-code-bubble)]">
          <div className="max-h-[250px] overflow-auto px-3 py-2 text-[13px] text-[color:var(--ui-text)]">
            {isLinear ? (
              <Markdown content={body} />
            ) : blocks ? (
              renderSlackBlocks(blocks)
            ) : (
              <div className="[overflow-wrap:anywhere] break-words whitespace-pre-wrap">
                {renderSlackBody(body)}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
})
