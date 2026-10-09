import {
  ChatCircleIcon,
  CheckCircleIcon,
  XCircleIcon,
} from "@phosphor-icons/react"

import type {
  ConversationAuthor,
  ConversationReviewState,
} from "@/features/reviews/lib/conversationApi"
import { cn, formatRelativeTime } from "@/lib/utils"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { displayName } from "@/features/reviews/lib/logins"
import { AgentMark } from "@/features/reviews/page/AgentMark"
import { Tag } from "@/features/reviews/page/Tag"

/** Someone's name, linked to their GitHub profile. */
export function ProfileLink({
  author,
  className,
}: {
  author: { login: string; bot?: boolean } | null
  className?: string
}) {
  if (!author) return <span className={className}>{displayName(author)}</span>
  return (
    <a
      href={githubUrls.profile(author)}
      target="_blank"
      rel="noreferrer"
      className={cn("hover:underline", className)}
    >
      {displayName(author)}
    </a>
  )
}

export const reviewStateWords: Record<ConversationReviewState, string> = {
  APPROVED: "approved",
  CHANGES_REQUESTED: "requested changes",
  COMMENTED: "reviewed",
  DISMISSED: "had a review dismissed",
}

export function ReviewStateMark({ state }: { state: ConversationReviewState }) {
  if (state === "APPROVED")
    return <CheckCircleIcon weight="fill" className="size-3.5 text-success" />
  if (state === "CHANGES_REQUESTED")
    return <XCircleIcon weight="fill" className="size-3.5 text-destructive" />
  return <ChatCircleIcon className="size-3.5 text-muted-foreground" />
}

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
  const day = new Date()
  if (date.toDateString() === day.toDateString()) return `today ${time}`
  day.setDate(day.getDate() - 1)
  if (date.toDateString() === day.toDateString()) return `yesterday ${time}`
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
  /** Empty while a comment is still being posted. */
  href?: string
  verb?: string
}) {
  const when = formatWhen(createdAt)
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-xs">
      <span className="truncate font-medium text-foreground">
        {displayName(author)}
      </span>
      {author?.bot && (
        <Tag
          title={
            author.posted_by
              ? `Posted through ${author.posted_by}'s GitHub account`
              : undefined
          }
        >
          {author.posted_by ? `via ${author.posted_by}` : "bot"}
        </Tag>
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
