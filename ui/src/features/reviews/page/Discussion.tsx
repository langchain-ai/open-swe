import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useRef, useState } from "react"
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
import { ThreadCard, ThreadSummary } from "./notes/ThreadNote"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"
import { plainFirstLine } from "./text"

type Said = ConversationComment | ConversationReview

type Block =
  | { kind: "commits"; key: string; commits: Array<ConversationCommit> }
  /** `earlier` holds the same message, said before, by the same bot. */
  | { kind: "item"; key: string; item: Said; earlier: Array<Said> }

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
    const folded =
      index >= 0
        ? (blocks.splice(index, 1)[0] as Extract<Block, { kind: "item" }>)
        : null
    blocks.push({
      kind: "item",
      key: `${item.kind}-${item.id}`,
      item,
      earlier: folded ? [...folded.earlier, folded.item] : [],
    })
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
  const threads = conversation.data?.threads
  // A reply is a review of its own on GitHub, so a review owns every thread it wrote in.
  const threadsByReview = useMemo(() => {
    const map = new Map<number, Array<ReviewThread>>()
    for (const thread of threads ?? []) {
      const reviews = new Set(
        thread.comments.flatMap((comment) =>
          comment.review_id === null ? [] : [comment.review_id]
        )
      )
      for (const review of reviews)
        map.set(review, [...(map.get(review) ?? []), thread])
    }
    return map
  }, [threads])
  const open = useMemo(
    () => (threads ?? []).filter((thread) => !thread.resolved),
    [threads]
  )

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
        ) : (
          <>
            {open.length > 0 && <OpenConversations pr={pr} threads={open} />}
            {blocks.length === 0 ? (
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
                      pr={pr}
                      item={block.item}
                      earlier={block.earlier}
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
          </>
        )}
      </div>
      <CommentBox pr={pr} />
    </section>
  )
}

/** Every thread still waiting on someone, at the top, each one open-able in place. */
function OpenConversations({
  pr,
  threads,
}: {
  pr: PullRequestRef
  threads: Array<ReviewThread>
}) {
  const focusKey = useReviewPage((state) => state.openConversationsKey)
  const ref = useRef<HTMLDivElement>(null)
  // Folding holds until something asks for the conversations again.
  const [foldedAt, setFoldedAt] = useState<number | null>(null)
  const folded = foldedAt === focusKey
  useEffect(() => {
    if (focusKey > 0)
      ref.current?.scrollIntoView({ block: "start", behavior: "smooth" })
  }, [focusKey])
  return (
    <div ref={ref} className="mb-5 scroll-mt-2">
      <button
        type="button"
        aria-expanded={!folded}
        onClick={() => setFoldedAt(folded ? null : focusKey)}
        className="mb-2 flex items-center gap-1.5 text-xs font-medium text-foreground"
      >
        <CaretRightIcon
          className={cn("size-3 transition-transform", !folded && "rotate-90")}
        />
        Open conversations
        <span className="rounded-full bg-muted px-1.5 text-[10px] text-muted-foreground tabular-nums">
          {threads.length}
        </span>
      </button>
      {!folded && (
        <ul className="flex flex-col gap-1.5">
          {threads.map((thread) => (
            <li key={thread.id}>
              <ThreadRow pr={pr} thread={thread} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** A thread as one line that opens into the whole thread, with the code it was left on. */
function ThreadRow({
  pr,
  thread,
}: {
  pr: PullRequestRef
  thread: ReviewThread
}) {
  const [open, setOpen] = useState(false)
  const line = thread.line ?? thread.original_line
  const name = thread.path.split("/").pop() ?? thread.path
  return open ? (
    <ThreadCard
      pr={pr}
      thread={thread}
      withContext
      onCollapse={() => setOpen(false)}
    />
  ) : (
    <ThreadSummary
      thread={thread}
      location={`${name}${line ? `:${line}` : ""}`}
      onOpen={() => setOpen(true)}
    />
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
        <CaretRightIcon
          className={cn(
            "ml-1 inline size-3 align-[-2px] transition-transform",
            open && "rotate-90"
          )}
        />
      </button>
      {open && (
        <ul className="mt-1 flex flex-col gap-0.5">
          {commits.map((commit) => (
            <li key={commit.sha}>
              <a
                href={commit.html_url}
                target="_blank"
                rel="noreferrer"
                className="group/commit flex items-center gap-2 text-muted-foreground hover:text-foreground"
              >
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] group-hover/commit:underline">
                  {commit.message.split("\n")[0]}
                </span>
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground/70">
                  {commit.sha.slice(0, 7)}
                </span>
              </a>
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
  pr,
  item,
  earlier,
  threads,
}: {
  pr: PullRequestRef
  item: Said
  earlier: Array<Said>
  threads: Array<ReviewThread>
}) {
  const bot = Boolean(item.author?.bot)
  const body = item.body.trim()
  const [open, setOpen] = useState(!bot)
  const [showEarlier, setShowEarlier] = useState(false)
  // A review with no words of its own only replied in threads; say where.
  const repliedOnly = item.kind === "review" && !body && threads.length > 0
  const verb =
    item.kind === "review"
      ? repliedOnly
        ? `replied in ${threads.length} thread${threads.length === 1 ? "" : "s"}`
        : stateWords[item.state]
      : "commented"
  return (
    <li className="relative pl-8">
      <Avatar
        author={item.author}
        className="absolute top-0 left-0 size-6 ring-4 ring-background"
      />
      <div className="flex min-w-0 items-center gap-1.5">
        {item.kind === "review" && !repliedOnly && (
          <ReviewStateMark state={item.state} />
        )}
        <Byline
          author={item.author}
          createdAt={item.created_at}
          href={item.html_url}
          verb={verb}
        />
        {earlier.length > 0 && (
          <button
            type="button"
            aria-expanded={showEarlier}
            onClick={() => setShowEarlier((value) => !value)}
            className="shrink-0 rounded px-1 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
            title="Said the same thing before"
          >
            ×{earlier.length + 1}
          </button>
        )}
      </div>
      {showEarlier && (
        <ul className="mt-1 flex flex-col gap-0.5 text-[11px] text-muted-foreground">
          {earlier.map((said) => (
            <li key={said.id}>
              <a
                href={said.html_url}
                target="_blank"
                rel="noreferrer"
                className="hover:text-foreground hover:underline"
              >
                Also {said.kind === "review" ? "reviewed" : "said"}{" "}
                {formatRelativeTime(new Date(said.created_at).getTime())}
              </a>
            </li>
          ))}
        </ul>
      )}
      {body &&
        (open ? (
          <div className="mt-1.5 rounded-lg border border-border bg-card px-3 py-2 text-[13px] leading-[1.6]">
            <Markdown content={item.body} />
            {bot && (
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="mt-1 text-[11px] text-muted-foreground hover:text-foreground"
              >
                Fold
              </button>
            )}
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="mt-0.5 block w-full truncate text-left text-xs text-muted-foreground hover:text-foreground"
          >
            {plainFirstLine(item.body)}
          </button>
        ))}
      {threads.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1">
          {threads.map((thread) => (
            <li key={thread.id}>
              <ThreadRow pr={pr} thread={thread} />
            </li>
          ))}
        </ul>
      )}
    </li>
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
