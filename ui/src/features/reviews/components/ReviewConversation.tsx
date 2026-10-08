import { Avatar } from "@langchain/macaw-components/Avatar"
import { Badge, type BadgeProps } from "@langchain/macaw-components/Badge"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { CheckCircleIcon } from "@phosphor-icons/react/dist/ssr/CheckCircle"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { ProhibitIcon } from "@phosphor-icons/react/dist/ssr/Prohibit"
import { XCircleIcon } from "@phosphor-icons/react/dist/ssr/XCircle"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useState } from "react"
import type { KeyboardEvent, ReactNode } from "react"

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
  icon: IconComponent
  filled?: boolean
  color: BadgeProps["color"]
  className?: string
}

const REVIEW_STATE_STYLES: Record<ConversationReviewState, StateStyle> = {
  APPROVED: {
    label: "Approved",
    verb: "approved these changes",
    icon: CheckCircleIcon,
    filled: true,
    color: "success",
  },
  CHANGES_REQUESTED: {
    label: "Changes requested",
    verb: "requested changes",
    icon: XCircleIcon,
    filled: true,
    color: "error",
  },
  COMMENTED: {
    label: "Commented",
    verb: "reviewed",
    icon: EyeIcon,
    color: "secondary",
  },
  DISMISSED: {
    label: "Dismissed",
    verb: "left a review that was dismissed",
    icon: ProhibitIcon,
    color: "secondary",
    className: "line-through",
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
    <Avatar
      size="md"
      shape="circle"
      className="mt-0.5"
      label={login}
      imageUrl={author?.avatar_url}
    />
  )
}

function Timestamp({ value, href }: { value: string; href: string }) {
  const date = new Date(value)
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-secondary hover:text-primary hover:underline"
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
      <div className="min-w-0 flex-1 overflow-hidden rounded-md border border-default">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-default bg-surface-level-2 px-3 py-2 text-xs">
          <span className="font-medium text-primary">{login}</span>
          <span className="text-secondary">
            {style ? style.verb : "commented"}
          </span>
          <Timestamp value={item.created_at} href={item.html_url} />
          <span className="ml-auto flex items-center gap-2">
            {review && review.inline_comment_count > 0 ? (
              <span className="flex items-center gap-1 text-secondary">
                <ChatCircleIcon weight="regular" className="size-3.5" />
                {review.inline_comment_count}{" "}
                {review.inline_comment_count === 1
                  ? "inline comment"
                  : "inline comments"}
              </span>
            ) : null}
            {style ? (
              <Badge
                size="sm"
                color={style.color}
                leftDecorator={style.icon}
                iconWeight={style.filled ? "fill" : "regular"}
                className={style.className}
              >
                {style.label}
              </Badge>
            ) : null}
            <IconButton
              asChild
              icon={ArrowSquareOutIcon}
              label="Open on GitHub"
              size="xs"
              color="secondary"
              variant="plain"
            >
              <a href={item.html_url} target="_blank" rel="noreferrer" />
            </IconButton>
          </span>
        </div>
        {hasBody ? (
          <div className="px-3 py-2 text-sm">
            <Markdown
              content={item.body}
              transformImageUrl={transformImageUrl}
              enlargeImages
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
        size="md"
        value={draft}
        onChange={setDraft}
        onKeyDown={onKeyDown}
        placeholder="Leave a comment"
        aria-label="Leave a comment"
        isError={mutation.isError}
        disabled={mutation.isPending}
        autoFocus
        rows={4}
      />
      {mutation.isError ? (
        <p role="alert" className="text-xs text-error-secondary">
          {mutation.error.message}
        </p>
      ) : null}
      <div className="flex items-center justify-end gap-2">
        <span className="text-xs text-secondary">
          Cmd/Ctrl + Enter to comment
        </span>
        <Button
          size="xs"
          color="secondary"
          variant="plain"
          onClick={onClose}
          disabled={mutation.isPending}
        >
          Cancel
        </Button>
        <Button type="submit" size="xs" disabled={!canSubmit}>
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
    timeline = <Banner intent="error">{query.error.message}</Banner>
  } else if (query.data.items.length === 0) {
    timeline = (
      <div className="flex flex-col items-center gap-2 rounded-md border border-dashed border-default px-4 py-8 text-center text-sm text-secondary">
        <ChatCircleIcon
          weight="regular"
          className="size-5 text-icon-tertiary"
        />
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
        <h2 className="text-sm font-medium">Conversation</h2>
        {!composing && (
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            leftDecorator={ChatCircleIcon}
            onClick={() => setComposing(true)}
          >
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
