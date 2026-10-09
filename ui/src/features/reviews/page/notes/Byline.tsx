import { CheckCircleFillIcon } from "@langchain/macaw-components/icons"
import { Avatar as MacawAvatar } from "@langchain/macaw-components/Avatar"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { XCircleIcon } from "@phosphor-icons/react/dist/ssr/XCircle"

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
    return (
      <CheckCircleFillIcon
        aria-hidden
        size={14}
        className="shrink-0 text-icon-success"
      />
    )
  if (state === "CHANGES_REQUESTED")
    return (
      <XCircleIcon
        aria-hidden
        size={14}
        weight="fill"
        className="shrink-0 text-icon-error"
      />
    )
  return (
    <ChatCircleIcon
      aria-hidden
      size={14}
      weight="regular"
      className="shrink-0 text-icon-secondary"
    />
  )
}

/** A person's avatar; bots are square, and what Open SWE posted as someone shows its mark. */
export function Avatar({
  author,
  size = "sm",
  className,
}: {
  author: ConversationAuthor | null
  size?: "xs" | "sm" | "md"
  className?: string
}) {
  return (
    <MacawAvatar
      size={size}
      shape={author?.bot ? "square" : "circle"}
      label={displayName(author)}
      imageUrl={author?.avatar_url || undefined}
      fallbackIcon={<AgentMark className="size-[70%]" />}
      className={cn("shrink-0", className)}
    />
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
      <span className="truncate font-medium text-primary">
        {displayName(author)}
      </span>
      {author?.bot && <Tag>bot</Tag>}
      {verb && <span className="text-secondary">{verb}</span>}
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 text-secondary hover:underline"
          title={new Date(createdAt).toLocaleString()}
        >
          {when}
        </a>
      ) : (
        <span className="shrink-0 text-secondary">{when}</span>
      )}
    </span>
  )
}
