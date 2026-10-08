import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useMemo, useState } from "react"
import {
  CaretRightIcon,
  CheckCircleIcon,
  ChatCircleIcon,
  GitCommitIcon,
  XCircleIcon,
} from "@phosphor-icons/react"

import {
  postReviewConversationComment,
  type Conversation,
  type ConversationAuthor,
  type ConversationComment,
  type ConversationCommit,
  type ConversationItem,
  type ConversationReview,
  type ReviewThread,
} from "@/features/reviews/lib/conversationApi"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { cn, formatRelativeTime } from "@/lib/utils"
import { Avatar, Byline } from "./notes/Byline"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"
import { plainFirstLine } from "./text"

type Block =
  | { kind: "commits"; key: string; commits: Array<ConversationCommit> }
  | {
      kind: "item"
      key: string
      item: ConversationComment | ConversationReview
      repeats: number
    }

const stateWords: Record<ConversationReview["state"], string> = {
  APPROVED: "approved",
  CHANGES_REQUESTED: "requested changes",
  COMMENTED: "reviewed",
  DISMISSED: "had a review dismissed",
}

/** Consecutive pushes become one block, and a bot repeating itself folds into its latest say. */
function toBlocks(items: ReadonlyArray<ConversationItem>): Array<Block> {
  const blocks: Array<Block> = []
  for (const item of items) {
    const last = blocks.at(-1)
    if (item.kind === "commit") {
      if (last?.kind === "commits") last.commits.push(item)
      else
        blocks.push({ kind: "commits", key: `c-${item.sha}`, commits: [item] })
      continue
    }
    // The repeat moves to where it was last said, so the timeline keeps reading forward.
    const index = blocks.findLastIndex(
      (block) =>
        block.kind === "item" &&
        !!item.author?.bot &&
        block.item.author?.login === item.author.login &&
        block.item.kind === item.kind &&
        plainFirstLine(block.item.body) === plainFirstLine(item.body)
    )
    const repeats =
      index >= 0
        ? (blocks.splice(index, 1)[0] as Extract<Block, { kind: "item" }>)
            .repeats + 1
        : 0
    blocks.push({ kind: "item", key: `${item.kind}-${item.id}`, item, repeats })
  }
  return blocks
}

/** GitHub's conversation, with people at full volume and bots folded to a line each. */
export function Discussion({ pr }: { pr: PullRequestRef }) {
  const conversation = useQuery(reviewQueries.conversation(pr))
  const blocks = useMemo(
    () => toBlocks(conversation.data?.items ?? []),
    [conversation.data?.items]
  )
  const threadsByReview = useMemo(() => {
    const map = new Map<number, Array<ReviewThread>>()
    for (const thread of conversation.data?.threads ?? []) {
      if (thread.review_id === null) continue
      map.set(thread.review_id, [...(map.get(thread.review_id) ?? []), thread])
    }
    return map
  }, [conversation.data?.threads])

  return (
    <section aria-label="Conversation" className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {conversation.isPending ? (
          <div className="flex flex-col gap-4">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={i} className="flex gap-2">
                <Skeleton className="size-6 rounded-full" />
                <Skeleton className="h-14 flex-1" />
              </div>
            ))}
          </div>
        ) : conversation.isError ? (
          <p className="text-xs text-destructive">
            Couldn&apos;t load the conversation: {conversation.error.message}
          </p>
        ) : blocks.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No one has commented yet.
          </p>
        ) : (
          <ol className="relative flex flex-col gap-3 before:absolute before:inset-y-2 before:left-[11px] before:w-px before:bg-border">
            {blocks.map((block) =>
              block.kind === "commits" ? (
                <CommitsBlock key={block.key} commits={block.commits} />
              ) : (
                <ItemBlock
                  key={block.key}
                  item={block.item}
                  repeats={block.repeats}
                  threads={
                    block.item.kind === "review"
                      ? (threadsByReview.get(block.item.id) ?? [])
                      : []
                  }
                />
              )
            )}
          </ol>
        )}
      </div>
      <CommentBox pr={pr} />
    </section>
  )
}

function CommitsBlock({ commits }: { commits: Array<ConversationCommit> }) {
  const [open, setOpen] = useState(commits.length <= 2)
  const authors = [
    ...new Set(commits.map((commit) => commit.author?.login ?? "someone")),
  ]
  return (
    <li className="relative pl-8 text-xs">
      <span className="absolute top-0.5 left-[5px] flex size-[13px] items-center justify-center rounded-full bg-background text-muted-foreground ring-4 ring-background">
        <GitCommitIcon className="size-3.5" />
      </span>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="text-left text-muted-foreground hover:text-foreground"
      >
        <span className="font-medium text-foreground/80">
          {authors.join(", ")}
        </span>{" "}
        pushed {commits.length} commit{commits.length === 1 ? "" : "s"}{" "}
        {formatRelativeTime(new Date(commits.at(-1)!.created_at).getTime())}
        {commits.length > 2 && (
          <CaretRightIcon
            className={cn(
              "ml-1 inline size-3 align-[-2px] transition-transform",
              open && "rotate-90"
            )}
          />
        )}
      </button>
      {open && (
        <ul className="mt-1 flex flex-col gap-0.5">
          {commits.map((commit) => (
            <li key={commit.sha} className="flex items-center gap-2">
              <a
                href={commit.html_url}
                target="_blank"
                rel="noreferrer"
                className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground hover:text-foreground hover:underline"
              >
                {commit.message.split("\n")[0]}
              </a>
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground/70">
                {commit.sha.slice(0, 7)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

function ReviewStateMark({ state }: { state: ConversationReview["state"] }) {
  if (state === "APPROVED")
    return <CheckCircleIcon weight="fill" className="size-3.5 text-success" />
  if (state === "CHANGES_REQUESTED")
    return <XCircleIcon weight="fill" className="size-3.5 text-destructive" />
  return <ChatCircleIcon className="size-3.5 text-muted-foreground" />
}

function ItemBlock({
  item,
  repeats,
  threads,
}: {
  item: ConversationComment | ConversationReview
  repeats: number
  threads: Array<ReviewThread>
}) {
  const bot = Boolean(item.author?.bot)
  const quiet = bot || (item.kind === "review" && !item.body.trim())
  const [open, setOpen] = useState(!quiet)
  const verb = item.kind === "review" ? stateWords[item.state] : "commented"
  const firstLine = plainFirstLine(item.body)
  return (
    <li className="relative pl-8">
      <Avatar
        author={item.author as ConversationAuthor | null}
        className="absolute top-0 left-0 size-6 ring-4 ring-background"
      />
      <div className="flex min-w-0 items-center gap-1.5">
        {item.kind === "review" && <ReviewStateMark state={item.state} />}
        <Byline
          author={item.author}
          createdAt={item.created_at}
          href={item.html_url}
          verb={verb}
        />
        {repeats > 0 && (
          <span
            className="shrink-0 text-[11px] text-muted-foreground"
            title="The same message, posted again"
          >
            ×{repeats + 1}
          </span>
        )}
      </div>
      {quiet && !open
        ? item.body.trim() && (
            <button
              type="button"
              onClick={() => setOpen(true)}
              className="mt-0.5 block w-full truncate text-left text-xs text-muted-foreground hover:text-foreground"
            >
              {firstLine}
            </button>
          )
        : item.body.trim() && (
            <div className="mt-1.5 rounded-lg border border-border bg-card px-3 py-2 text-[13px] leading-[1.6]">
              <Markdown content={item.body} />
            </div>
          )}
      {threads.length > 0 && <ReviewThreads threads={threads} />}
    </li>
  )
}

function ReviewThreads({ threads }: { threads: Array<ReviewThread> }) {
  const jumpTo = useReviewPage((state) => state.jumpTo)
  return (
    <ul className="mt-1.5 flex flex-col gap-0.5">
      {threads.map((thread) => {
        const line = thread.line ?? thread.original_line
        const name = thread.path.split("/").pop()
        return (
          <li key={thread.id}>
            <button
              type="button"
              disabled={thread.outdated || thread.line === null}
              onClick={() =>
                thread.line !== null &&
                jumpTo({
                  kind: "line",
                  path: thread.path,
                  line: thread.line,
                  side: thread.side,
                })
              }
              className={cn(
                "flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-xs hover:bg-accent disabled:hover:bg-transparent",
                thread.resolved && "opacity-60"
              )}
              title={thread.path}
            >
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                {name}
                {line ? `:${line}` : ""}
              </span>
              <span className="min-w-0 flex-1 truncate">
                {plainFirstLine(thread.comments[0]?.body ?? "")}
              </span>
              {thread.comments.length > 1 && (
                <span className="shrink-0 text-[10px] text-muted-foreground tabular-nums">
                  {thread.comments.length}
                </span>
              )}
              {thread.outdated && (
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  Outdated
                </span>
              )}
              {thread.resolved && (
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  Resolved
                </span>
              )}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

function CommentBox({ pr }: { pr: PullRequestRef }) {
  const queryClient = useQueryClient()
  const [body, setBody] = useState("")
  const key = reviewQueries.conversation(pr).queryKey
  const post = useMutation({
    mutationFn: (text: string) =>
      postReviewConversationComment(pr.owner, pr.repo, pr.number, text),
    meta: { errorTitle: "Couldn't post the comment", silent: true },
    onSuccess: (comment) => {
      queryClient.setQueryData<Conversation | undefined>(key, (old) =>
        old ? { ...old, items: [...old.items, comment] } : old
      )
      setBody("")
      void queryClient.invalidateQueries({ queryKey: key })
    },
  })
  const send = () => {
    const text = body.trim()
    if (text && !post.isPending) post.mutate(text)
  }
  return (
    <div className="border-t border-border p-3">
      <Textarea
        aria-label="Comment on this pull request"
        placeholder="Comment on this pull request"
        rows={2}
        value={body}
        onChange={(event) => setBody(event.target.value)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault()
            send()
          }
        }}
        className="max-h-48 resize-none text-xs"
      />
      {post.error && (
        <p role="alert" className="mt-1 text-xs text-destructive">
          {post.error.message}
        </p>
      )}
      <div className="mt-2 flex justify-end">
        <Button
          size="sm"
          disabled={!body.trim() || post.isPending}
          onClick={send}
        >
          {post.isPending ? "Commenting…" : "Comment"}
        </Button>
      </div>
    </div>
  )
}
