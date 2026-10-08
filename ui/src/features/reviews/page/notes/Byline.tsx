import type { ConversationAuthor } from "@/features/reviews/lib/conversationApi"
import { cn, formatRelativeTime } from "@/lib/utils"
import { AgentMark } from "@/features/reviews/page/AgentMark"

export function Avatar({
  author,
  className,
}: {
  author: ConversationAuthor | null
  className?: string
}) {
  if (author?.posted_by)
    return (
      <span
        className={cn(
          "flex size-5 shrink-0 items-center justify-center rounded-[5px] bg-muted",
          className
        )}
      >
        <AgentMark className="size-[70%]" />
      </span>
    )
  return author?.avatar_url ? (
    <img
      src={author.avatar_url}
      alt=""
      loading="lazy"
      className={cn(
        "size-5 shrink-0 bg-muted",
        author.bot ? "rounded-[5px]" : "rounded-full",
        className
      )}
    />
  ) : (
    <span className={cn("size-5 shrink-0 rounded-full bg-muted", className)} />
  )
}

/** Relative while recent; past a few hours, the day and time, so a busy day's events stay apart. */
export function formatWhen(createdAt: string): string {
  const date = new Date(createdAt)
  if (Date.now() - date.getTime() < 6 * 3_600_000)
    return formatRelativeTime(date.getTime())
  const time = date.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  })
  const yesterday = new Date()
  yesterday.setDate(yesterday.getDate() - 1)
  if (date.toDateString() === new Date().toDateString()) return `today ${time}`
  if (date.toDateString() === yesterday.toDateString())
    return `yesterday ${time}`
  return `${date.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`
}

/** Who said it and when: the line above every comment. Bots are marked, as on GitHub. */
export function Byline({
  author,
  createdAt,
  href,
  verb,
}: {
  author: ConversationAuthor | null
  createdAt: string
  href?: string
  verb?: string
}) {
  const when = formatWhen(createdAt)
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-xs">
      <span className="truncate font-medium text-foreground">
        {author?.login.replace(/\[bot\]$/, "") ?? "ghost"}
      </span>
      {author?.bot && (
        <span
          title={
            author.posted_by
              ? `Posted through ${author.posted_by}'s GitHub account`
              : undefined
          }
          className="rounded-[4px] border border-border px-1 text-[10px] leading-4 text-muted-foreground"
        >
          {author.posted_by ? `via ${author.posted_by}` : "bot"}
        </span>
      )}
      {verb && <span className="text-muted-foreground">{verb}</span>}
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 text-muted-foreground hover:underline"
          title={new Date(createdAt).toLocaleString()}
        >
          {when}
        </a>
      ) : (
        <span className="shrink-0 text-muted-foreground">{when}</span>
      )}
    </span>
  )
}
