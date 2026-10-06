import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useId, useState } from "react"
import type { KeyboardEvent, ReactNode } from "react"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Avatar } from "@langchain/gtm-platform-design-system/ui/avatar"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import {
  Button,
  buttonVariants,
} from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"

import {
  AlertTriangle,
  Ban,
  CheckCircle,
  ExternalLink,
  Eye,
  MessageSquare,
  XCircle,
  type Glyph,
} from "@/components/glyphs"
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
  icon: Glyph
  tone: "positive" | "risk" | "neutral"
  className?: string
}

const REVIEW_STATE_STYLES: Record<ConversationReviewState, StateStyle> = {
  APPROVED: {
    label: "Approved",
    verb: "approved these changes",
    icon: CheckCircle,
    tone: "positive",
  },
  CHANGES_REQUESTED: {
    label: "Changes requested",
    verb: "requested changes",
    icon: XCircle,
    tone: "risk",
  },
  COMMENTED: {
    label: "Commented",
    verb: "reviewed",
    icon: Eye,
    tone: "neutral",
  },
  DISMISSED: {
    label: "Dismissed",
    verb: "left a review that was dismissed",
    icon: Ban,
    tone: "neutral",
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
  return (
    <Avatar
      name={author?.login ?? "ghost"}
      src={author?.avatar_url}
      size="chat"
      className="mt-1.5"
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
    <Inline render={<li />} gap="md" align="start">
      <AuthorAvatar author={item.author} />
      <Stack
        border="line"
        radius="compact"
        bg="panel"
        className="min-w-0 flex-1 overflow-hidden"
      >
        <Inline
          gap="sm"
          wrap
          bg="muted"
          className="border-b border-line px-3 py-2 text-label"
        >
          <span className="font-medium text-ink">{login}</span>
          <span className="text-ink-subtle">
            {style ? style.verb : "commented"}
          </span>
          <Timestamp value={item.created_at} href={item.html_url} />
          <Inline gap="sm" className="ml-auto">
            {review && review.inline_comment_count > 0 ? (
              <Inline gap="xs" className="text-meta text-ink-subtle">
                <Icon icon={MessageSquare} size="sm" />
                {review.inline_comment_count}{" "}
                {review.inline_comment_count === 1
                  ? "inline comment"
                  : "inline comments"}
              </Inline>
            ) : null}
            {style ? (
              <Badge tier="quiet" tone={style.tone} className={style.className}>
                <Icon icon={style.icon} size="sm" />
                {style.label}
              </Badge>
            ) : null}
            <a
              href={item.html_url}
              target="_blank"
              rel="noreferrer"
              aria-label="Open on GitHub"
              className={cn(
                buttonVariants({ variant: "ghost", size: "icon-sm" }),
                "text-ink-subtle hover:text-ink"
              )}
            >
              <Icon icon={ExternalLink} size="sm" />
            </a>
          </Inline>
        </Inline>
        {hasBody ? (
          <Box className="px-3 py-2 text-body">
            <Markdown
              content={item.body}
              transformImageUrl={transformImageUrl}
            />
          </Box>
        ) : null}
      </Stack>
    </Inline>
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
    <Stack
      render={
        <form
          onSubmit={(event) => {
            event.preventDefault()
            submit()
          }}
        />
      }
      gap="sm"
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
        <Inline
          role="alert"
          gap="xs"
          align="start"
          className="text-label text-risk"
        >
          <Icon icon={AlertTriangle} size="sm" className="mt-0.5" />
          <span>{mutation.error.message}</span>
        </Inline>
      ) : null}
      <Inline gap="sm" justify="end">
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
        <Button
          type="submit"
          size="compact"
          disabled={!canSubmit}
          loading={mutation.isPending}
        >
          Comment
        </Button>
      </Inline>
    </Stack>
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

  const headingId = useId()

  let timeline: ReactNode
  if (query.isPending) {
    timeline = (
      <Stack gap="md">
        <Skeleton className="h-20 w-full rounded-compact" />
        <Skeleton className="h-20 w-full rounded-compact" />
      </Stack>
    )
  } else if (query.isError) {
    timeline = (
      <StateNotice
        tone="RISK"
        icon={AlertTriangle}
        title="Could not load the conversation"
        description={query.error.message}
      />
    )
  } else if (query.data.items.length === 0) {
    timeline = (
      <EmptyState
        icon={MessageSquare}
        title="No comments or reviews yet."
        className="rounded-panel border border-dashed border-line"
      />
    )
  } else {
    timeline = (
      <Stack render={<ol />} gap="lg">
        {query.data.items.map((item) => (
          <TimelineEntry
            key={`${item.kind}-${item.id}`}
            item={item}
            transformImageUrl={transformImageUrl}
          />
        ))}
      </Stack>
    )
  }

  return (
    <Stack
      render={<section aria-labelledby={headingId} />}
      gap="md"
      className={className}
    >
      <Inline gap="sm" justify="between">
        <Box
          render={<h2 id={headingId} />}
          className="text-title font-medium text-ink"
        >
          Conversation
        </Box>
        {!composing && (
          <Button
            type="button"
            size="compact"
            variant="outline"
            onClick={() => setComposing(true)}
          >
            <Icon icon={MessageSquare} size="sm" />
            Comment
          </Button>
        )}
      </Inline>
      {composing && (
        <CommentBox
          owner={owner}
          repo={repo}
          number={number}
          onClose={() => setComposing(false)}
        />
      )}
      {timeline}
    </Stack>
  )
}
