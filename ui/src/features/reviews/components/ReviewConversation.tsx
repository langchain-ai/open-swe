import {
  ArrowSquareOutIcon,
  ChatCircleIcon,
  CheckCircleIcon,
  EyeIcon,
  ProhibitIcon,
  XCircleIcon,
} from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useState } from "react"
import type { KeyboardEvent, ReactNode } from "react"

import { Alert, AlertDescription } from "@langchain/gtm-platform-design-system/ui/alert"
import { Avatar, AvatarFallback, AvatarImage } from "@langchain/gtm-platform-design-system/ui/avatar"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import {
  getReviewConversation,
  postReviewConversationComment,
} from "@/features/reviews/lib/conversationApi"
import type {
  ConversationAuthor,
  ConversationItem,
  ConversationReviewState,
} from "@/features/reviews/lib/conversationApi"
import { reviewImageProxyUrl } from "@/lib/api"
import { cn, formatRelativeTime } from "@/lib/utils"

interface ReviewConversationProps {
  owner: string
  repo: string
  number: number
}

interface StateStyle {
  label: string
  verb: string
  icon: ReactNode
  className: string
}

const REVIEW_STATE_STYLES: Record<ConversationReviewState, StateStyle> = {
  APPROVED: {
    label: "Approved",
    verb: "approved these changes",
    icon: <CheckCircleIcon weight="fill" />,
    className:
      "bg-positive-bg text-positive",
  },
  CHANGES_REQUESTED: {
    label: "Changes requested",
    verb: "requested changes",
    icon: <XCircleIcon weight="fill" />,
    className: "bg-risk-bg text-risk",
  },
  COMMENTED: {
    label: "Commented",
    verb: "reviewed",
    icon: <EyeIcon />,
    className: "bg-muted text-ink-subtle",
  },
  DISMISSED: {
    label: "Dismissed",
    verb: "left a review that was dismissed",
    icon: <ProhibitIcon />,
    className: "bg-muted text-ink-subtle line-through",
  },
}

export function reviewConversationQueryKey(
  owner: string,
  repo: string,
  number: number
) {
  return ["review-conversation", owner, repo, number] as const
}

function AuthorAvatar({ author }: { author: ConversationAuthor | null }) {
  const login = author?.login ?? "ghost"
  return (
    <Avatar size="sm" className="mt-0.5">
      {author ? <AvatarImage src={author.avatar_url} alt={login} /> : null}
      <AvatarFallback>{login.slice(0, 2).toUpperCase()}</AvatarFallback>
    </Avatar>
  )
}

function Timestamp({ value, href }: { value: string; href: string }) {
  const date = new Date(value)
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-ink-subtle hover:text-ink hover:underline"
    >
      <time
        dateTime={value}
        title={date.toLocaleString()}
        suppressHydrationWarning
      >
        {formatRelativeTime(date.getTime())}
      </time>
    </a>
  )
}

function TimelineEntry({
  item,
  transformImageUrl,
}: {
  item: ConversationItem
  transformImageUrl: (src: string) => string
}) {
  const login = item.author?.login ?? "ghost"
  const review = item.kind === "review" ? item : null
  const style = review ? REVIEW_STATE_STYLES[review.state] : null
  const hasBody = item.body.trim().length > 0

  return (
    <li className="flex gap-3">
      <AuthorAvatar author={item.author} />
      <div className="min-w-0 flex-1 overflow-hidden rounded-badge border border-line">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line bg-muted/40 px-3 py-2 text-label">
          <span className="font-medium text-ink">{login}</span>
          <span className="text-ink-subtle">
            {style ? style.verb : "commented"}
          </span>
          <Timestamp value={item.created_at} href={item.html_url} />
          <span className="ml-auto flex items-center gap-2">
            {review && review.inline_comment_count > 0 ? (
              <span className="flex items-center gap-1 text-ink-subtle">
                <ChatCircleIcon />
                {review.inline_comment_count}{" "}
                {review.inline_comment_count === 1
                  ? "inline comment"
                  : "inline comments"}
              </span>
            ) : null}
            {style ? (
              <Badge className={cn("gap-1", style.className)}>
                {style.icon}
                {style.label}
              </Badge>
            ) : null}
            <a
              href={item.html_url}
              target="_blank"
              rel="noreferrer"
              aria-label="Open on GitHub"
              className="text-ink-subtle hover:text-ink"
            >
              <ArrowSquareOutIcon />
            </a>
          </span>
        </div>
        {hasBody ? (
          <div className="px-3 py-2 text-body">
            <Markdown
              content={item.body}
              transformImageUrl={transformImageUrl}
            />
          </div>
        ) : null}
      </div>
    </li>
  )
}

function CommentBox({
  owner,
  repo,
  number,
  onClose,
}: ReviewConversationProps & { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState("")
  const mutation = useMutation({
    mutationFn: (body: string) =>
      postReviewConversationComment(owner, repo, number, body),
    meta: { errorTitle: "Couldn't post the comment", silent: true },
    onSuccess: async () => {
      setDraft("")
      onClose()
      await queryClient.invalidateQueries({
        queryKey: reviewConversationQueryKey(owner, repo, number),
      })
    },
  })
  const canSubmit = draft.trim().length > 0 && !mutation.isPending

  const submit = () => {
    if (canSubmit) mutation.mutate(draft.trim())
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault()
      submit()
    } else if (event.key === "Escape" && !mutation.isPending) {
      event.preventDefault()
      onClose()
    }
  }

  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <Textarea
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Leave a comment"
        aria-label="Leave a comment"
        aria-invalid={mutation.isError || undefined}
        disabled={mutation.isPending}
        autoFocus
        className="min-h-24"
      />
      {mutation.isError ? (
        <p role="alert" className="text-label text-risk">
          {mutation.error.message}
        </p>
      ) : null}
      <div className="flex items-center justify-end gap-2">
        <span className="text-meta text-ink-subtle">
          Cmd/Ctrl + Enter to comment
        </span>
        <Button
          type="button"
          size="compact"
          variant="ghost"
          onClick={onClose}
          disabled={mutation.isPending}
        >
          Cancel
        </Button>
        <Button type="submit" size="compact" disabled={!canSubmit}>
          {mutation.isPending ? "Commenting…" : "Comment"}
        </Button>
      </div>
    </form>
  )
}

export function ReviewConversation({
  owner,
  repo,
  number,
  className,
}: ReviewConversationProps & { className?: string }) {
  const [composing, setComposing] = useState(false)
  const query = useQuery({
    queryKey: reviewConversationQueryKey(owner, repo, number),
    queryFn: () => getReviewConversation(owner, repo, number),
  })
  const transformImageUrl = useCallback(
    (src: string) => reviewImageProxyUrl(owner, repo, number, src),
    [owner, repo, number]
  )

  let timeline: ReactNode
  if (query.isPending) {
    timeline = (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    )
  } else if (query.isError) {
    timeline = (
      <Alert variant="error">
        <AlertDescription>{query.error.message}</AlertDescription>
      </Alert>
    )
  } else if (query.data.items.length === 0) {
    timeline = (
      <div className="flex flex-col items-center gap-2 rounded-badge border border-dashed border-line px-4 py-8 text-center text-body text-ink-subtle">
        <ChatCircleIcon className="size-5" />
        No comments or reviews yet.
      </div>
    )
  } else {
    timeline = (
      <ol className="flex flex-col gap-4">
        {query.data.items.map((item) => (
          <TimelineEntry
            key={`${item.kind}-${item.id}`}
            item={item}
            transformImageUrl={transformImageUrl}
          />
        ))}
      </ol>
    )
  }

  return (
    <section
      aria-label="Conversation"
      className={cn("flex flex-col gap-3", className)}
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-body font-medium">Conversation</h2>
        {!composing && (
          <Button
            type="button"
            size="compact"
            variant="outline"
            onClick={() => setComposing(true)}
          >
            <ChatCircleIcon />
            Comment
          </Button>
        )}
      </div>
      {composing && (
        <CommentBox
          owner={owner}
          repo={repo}
          number={number}
          onClose={() => setComposing(false)}
        />
      )}
      {timeline}
    </section>
  )
}
