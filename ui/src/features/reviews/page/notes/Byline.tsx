import type { ConversationAuthor } from "@/features/reviews/lib/conversationApi"
import { cn, formatRelativeTime } from "@/lib/utils"

export function Avatar({
  author,
  className,
}: {
  author: ConversationAuthor | null
  className?: string
}) {
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
  const when = formatRelativeTime(new Date(createdAt).getTime())
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-xs">
      <span className="truncate font-medium text-foreground">
        {author?.login.replace(/\[bot\]$/, "") ?? "ghost"}
      </span>
      {author?.bot && (
        <span className="rounded-[4px] border border-border px-1 text-[10px] leading-4 text-muted-foreground">
          bot
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
