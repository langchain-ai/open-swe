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

export const ReplyCard = memo(function ReplyCard({ chunk }: ReplyCardProps) {
  const isLinear = chunk.toolKind === "linear"
  const body =
    ((isLinear ? chunk.input?.comment_body : chunk.input?.message) as string) ||
    ""

  return (
    <div className="my-1">
      <div className="flex items-center gap-1.5 py-1 text-[12px] text-[color:var(--ui-text-muted)]">
        <MessageCircle className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span>{headerLabel(isLinear, chunk.status)}</span>
      </div>
      {body && (
        <div className="overflow-hidden rounded-xl bg-[var(--ui-accent-bubble)]">
          <div className="max-h-[250px] overflow-auto px-3 py-2 text-[13px] text-[color:var(--ui-text)]">
            {isLinear ? (
              <Markdown content={body} />
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
