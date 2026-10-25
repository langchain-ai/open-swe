import { XIcon } from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"

import type { QuoteReply } from "@/features/agents/utils/quoteReply"

export interface PendingReply {
  id: number
  reply: QuoteReply
}

function passageOf(reply: QuoteReply): string {
  return reply.kind === "code"
    ? `${reply.excerpt.path}:${reply.excerpt.lineLabel}\n${reply.excerpt.snippet}`
    : reply.quote
}

/** The replies riding on the next message, one card each, removable before sending. */
export function ComposerReplyCards({
  replies,
  onRemove,
}: {
  replies: ReadonlyArray<PendingReply>
  onRemove: (id: number) => void
}) {
  if (replies.length === 0) return null
  return (
    <ul
      aria-label="Replies in your next message"
      className="mb-space-2 flex flex-wrap gap-space-2"
    >
      {replies.map(({ id, reply }) => (
        <li
          key={id}
          title={passageOf(reply)}
          className="group relative flex h-24 w-36 flex-col justify-between rounded-lg border border-default bg-surface-level-2 p-space-2"
        >
          <div className="flex min-h-0 flex-col gap-0.5 border-l-2 border-default pl-space-2">
            {reply.kind === "code" && (
              <span className="truncate font-mono text-xxs text-tertiary">
                {reply.excerpt.path.split("/").pop()}:{reply.excerpt.lineLabel}
              </span>
            )}
            <p className="line-clamp-3 text-xs [overflow-wrap:anywhere] whitespace-pre-wrap text-secondary">
              {reply.comment ||
                (reply.kind === "code" ? reply.excerpt.path : reply.quote)}
            </p>
          </div>
          <span className="self-start rounded-full border border-default px-space-2 text-xxs text-secondary">
            Reply
          </span>
          <IconButton
            className="absolute -top-1.5 -right-1.5 opacity-0 group-focus-within:opacity-100 group-hover:opacity-100"
            color="secondary"
            icon={XIcon}
            label="Remove reply"
            onClick={() => onRemove(id)}
            round
            size="xs"
            variant="outlined"
          />
        </li>
      ))}
    </ul>
  )
}
