import {
  ArrowDownIcon,
  CaretRightIcon,
  CheckIcon,
} from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useRef, useState } from "react"
import { GitCommitIcon } from "@phosphor-icons/react/dist/ssr/GitCommit"

import {
  postReviewConversationComment,
  type Conversation,
  type ConversationAuthor,
  type ConversationComment,
  type ConversationCommit,
  type ConversationItem,
  type ConversationReview,
  type ReviewThread,
  type ThreadComment,
} from "@/features/reviews/lib/conversationApi"
import { sameLogin } from "@/features/reviews/lib/logins"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { cn } from "@/lib/utils"
import { threadLocation } from "./findings"
import {
  Avatar,
  Byline,
  formatWhen,
  ReviewStateMark,
  reviewStateWords,
} from "./notes/Byline"
import { ThreadCard, ThreadSummary } from "./notes/ThreadNote"
import {
  reviewQueries,
  useOpenConversations,
  type PullRequestRef,
} from "./queries"
import { useReviewPage } from "./store"
import { useResolveThreads } from "./useThreadActions"
import { plainFirstLine, plural } from "./text"

type Said = ConversationComment | ConversationReview

/** A reply someone left in an existing thread. */
interface Reply {
  thread: ReviewThread
  comment: ThreadComment
}

interface ReviewParts {
  /** Threads this review opened. */
  started: Array<ReviewThread>
  /** Its comments in threads that someone else opened. */
  replies: Array<Reply>
}

type Block =
  | { kind: "commits"; key: string; commits: Array<ConversationCommit> }
  /** `earlier` holds the same message, said before, by the same bot. */
  | {
      kind: "item"
      key: string
      item: Said
      earlier: Array<Said>
      parts: ReviewParts
    }
  /** Back-to-back reviews by one person that only replied in threads. */
  | {
      kind: "replies"
      key: string
      author: ConversationAuthor | null
      reviews: Array<ConversationReview>
      replies: Array<Reply>
    }

// Sits on the timeline's rule, ringed in the page colour so the rule breaks around it.
const TIMELINE_AVATAR = "absolute top-0 left-0 ring-4 ring-surface-level-1"

const NO_PARTS: ReviewParts = { started: [], replies: [] }

function reviewParts(
  threads: ReadonlyArray<ReviewThread>
): Map<number, ReviewParts> {
  const parts = new Map<number, ReviewParts>()
  const of = (id: number) => {
    let entry = parts.get(id)
    if (!entry) {
      entry = { started: [], replies: [] }
      parts.set(id, entry)
    }
    return entry
  }
  for (const thread of threads)
    thread.comments.forEach((comment, index) => {
      if (comment.review_id === null) return
      if (index === 0) of(comment.review_id).started.push(thread)
      else of(comment.review_id).replies.push({ thread, comment })
    })
  return parts
}

function inThreads(parts: ReviewParts): boolean {
  return parts.started.length > 0 || parts.replies.length > 0
}

/**
 * Every thread appears once, under the review that opened it. Replies-only
 * reviews by one person run together, pushes merge, and a bot repeating the
 * same words folds into its latest say.
 */
function toBlocks(
  items: ReadonlyArray<ConversationItem>,
  threads: ReadonlyArray<ReviewThread>
): Array<Block> {
  const partsByReview = reviewParts(threads)
  const blocks: Array<Block> = []
  for (const item of items) {
    const last = blocks.at(-1)
    if (item.kind === "commit") {
      if (last?.kind === "commits") last.commits.push(item)
      else
        blocks.push({ kind: "commits", key: `c-${item.sha}`, commits: [item] })
      continue
    }
    const parts =
      item.kind === "review"
        ? (partsByReview.get(item.id) ?? NO_PARTS)
        : NO_PARTS
    const body = item.body.trim()
    if (
      item.kind === "review" &&
      !body &&
      parts.started.length === 0 &&
      parts.replies.length > 0
    ) {
      if (
        last?.kind === "replies" &&
        last.author?.login === item.author?.login
      ) {
        last.reviews.push(item)
        last.replies.push(...parts.replies)
      } else
        blocks.push({
          kind: "replies",
          key: `r-${item.id}`,
          author: item.author,
          reviews: [item],
          replies: [...parts.replies],
        })
      continue
    }
    // Only a bot's spoken words fold; what it left in threads is never the same thing twice.
    const index =
      item.author?.bot && body && !inThreads(parts)
        ? blocks.findLastIndex(
            (block) =>
              block.kind === "item" &&
              !inThreads(block.parts) &&
              block.item.author?.login === item.author?.login &&
              block.item.kind === item.kind &&
              plainFirstLine(block.item.body) === plainFirstLine(item.body)
          )
        : -1
    // The repeat moves to where it was last said, so the timeline keeps reading forward.
    const folded =
      index >= 0
        ? (blocks.splice(index, 1)[0] as Extract<Block, { kind: "item" }>)
        : null
    blocks.push({
      kind: "item",
      key: `${item.kind}-${item.id}`,
      item,
      earlier: folded ? [...folded.earlier, folded.item] : [],
      parts,
    })
  }
  // Folding can leave pushes that were apart side by side.
  return blocks.reduce<Array<Block>>((merged, block) => {
    const previous = merged.at(-1)
    if (block.kind === "commits" && previous?.kind === "commits")
      previous.commits.push(...block.commits)
    else merged.push(block)
    return merged
  }, [])
}

/** GitHub's conversation, with people at full volume and bots folded to a line each. */
export function Discussion({ pr }: { pr: PullRequestRef }) {
  const conversation = useQuery(reviewQueries.conversation(pr))
  const threads = conversation.data?.threads
  const blocks = useMemo(
    () => toBlocks(conversation.data?.items ?? [], threads ?? []),
    [conversation.data?.items, threads]
  )
  const open = useOpenConversations(pr)?.threads ?? []
  const scroller = useRef<HTMLDivElement>(null)
  const [awayFromLatest, setAwayFromLatest] = useState(false)
  const measure = (node: HTMLElement) =>
    setAwayFromLatest(
      node.scrollHeight - node.scrollTop - node.clientHeight > 400
    )
  // The tab mounts hidden, so measure again whenever it is shown or its content grows.
  useEffect(() => {
    const node = scroller.current
    if (!node) return
    const observer = new ResizeObserver(() => measure(node))
    observer.observe(node)
    if (node.firstElementChild) observer.observe(node.firstElementChild)
    return () => observer.disconnect()
  }, [blocks])

  return (
    <section
      aria-label="Conversation"
      className="relative flex h-full min-h-0 flex-col"
    >
      <div
        ref={scroller}
        onScroll={(event) => measure(event.currentTarget)}
        className="min-h-0 flex-1 overflow-y-auto px-space-4 py-space-4"
      >
        {conversation.isPending ? (
          <div className="flex flex-col gap-space-4">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={i} className="flex gap-space-2">
                <Skeleton className="size-6 rounded-full" />
                <Skeleton className="h-14 flex-1" />
              </div>
            ))}
          </div>
        ) : conversation.isError ? (
          <p className="text-xs text-error-secondary">
            Couldn&apos;t load the conversation: {conversation.error.message}
          </p>
        ) : (
          <>
            {open.length > 0 && <OpenConversations pr={pr} threads={open} />}
            {blocks.length === 0 ? (
              <p className="text-xs text-secondary">
                No one has commented yet.
              </p>
            ) : (
              <ol className="relative flex flex-col gap-space-3 before:absolute before:inset-y-2 before:left-[11px] before:border-l before:border-default">
                {blocks.map((block) =>
                  block.kind === "commits" ? (
                    <CommitsBlock key={block.key} commits={block.commits} />
                  ) : block.kind === "replies" ? (
                    <RepliesBlock
                      key={block.key}
                      pr={pr}
                      author={block.author}
                      reviews={block.reviews}
                      replies={block.replies}
                    />
                  ) : (
                    <ItemBlock
                      key={block.key}
                      pr={pr}
                      item={block.item}
                      earlier={block.earlier}
                      parts={block.parts}
                    />
                  )
                )}
              </ol>
            )}
          </>
        )}
      </div>
      {awayFromLatest && (
        <button
          type="button"
          onClick={() =>
            scroller.current?.scrollTo({
              top: scroller.current.scrollHeight,
              behavior: "smooth",
            })
          }
          className="flex shrink-0 items-center justify-center gap-space-1 border-t border-default py-space-1 text-xxs font-medium text-secondary hover:bg-surface-level-1-hover hover:text-primary"
        >
          Jump to the latest
          <ArrowDownIcon className="size-3" />
        </button>
      )}
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
  const author = useQuery(reviewQueries.detail(pr)).data?.pr.author?.login
  const resolveThreads = useResolveThreads(pr)
  // The code moved on and the author had the last word: almost always done.
  const answered = threads.filter(
    (thread) =>
      thread.outdated &&
      thread.node_id &&
      sameLogin(thread.comments.at(-1)?.author?.login, author)
  )
  // Folding holds until something asks for the conversations again.
  const [foldedAt, setFoldedAt] = useState<number | null>(null)
  const folded = foldedAt === focusKey
  useEffect(() => {
    if (focusKey > 0)
      ref.current?.scrollIntoView({ block: "start", behavior: "smooth" })
  }, [focusKey])
  return (
    <div ref={ref} className="mb-space-4 scroll-mt-2">
      <div className="mb-space-2 flex items-center gap-space-2">
        <button
          type="button"
          aria-expanded={!folded}
          onClick={() => setFoldedAt(folded ? null : focusKey)}
          className="flex items-center gap-space-2 text-xs font-medium text-primary"
        >
          <CaretRightIcon
            className={cn(
              "size-3 transition-transform",
              !folded && "rotate-90"
            )}
          />
          Open conversations
          <span className="rounded-full bg-surface-level-3 px-space-1 text-xxs text-secondary tabular-nums">
            {threads.length}
          </span>
        </button>
        {answered.length > 0 && (
          <Button
            size="xs"
            variant="plain"
            color="secondary"
            className="ml-auto"
            loading={resolveThreads.isPending}
            disabled={resolveThreads.isPending}
            onClick={() => resolveThreads.mutate(answered)}
            title="Outdated conversations where the author replied last"
            aria-label={`Resolve ${answered.length} answered`}
          >
            Resolve {answered.length} answered
          </Button>
        )}
      </div>
      {!folded && (
        <ul className="flex flex-col gap-space-2">
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
      location={threadLocation(thread)}
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
    <li className="relative pl-space-6 text-xs">
      <span className="absolute top-0.5 left-[5px] flex size-[13px] items-center justify-center rounded-full bg-surface-level-1 text-secondary ring-4 ring-surface-level-1">
        <GitCommitIcon className="size-3.5" weight="regular" />
      </span>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="text-left text-secondary hover:text-primary"
      >
        <span className="font-medium text-primary">{authors.join(", ")}</span>{" "}
        pushed {plural(commits.length, "commit")}{" "}
        {formatWhen(commits.at(-1)!.created_at)}
        <CaretRightIcon
          className={cn(
            "ml-space-1 inline size-3 align-[-2px] transition-transform",
            open && "rotate-90"
          )}
        />
      </button>
      {open && (
        <ul className="mt-space-1 flex flex-col gap-0.5">
          {commits.map((commit) => (
            <li key={commit.sha}>
              <a
                href={commit.html_url}
                target="_blank"
                rel="noreferrer"
                className="group/commit flex items-center gap-space-2 text-secondary hover:text-primary"
              >
                <span className="min-w-0 flex-1 truncate font-mono text-xxs group-hover/commit:underline">
                  {commit.message.split("\n")[0]}
                </span>
                <span className="shrink-0 font-mono text-xxs text-tertiary">
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

/** One person's run of thread replies: where each went and what it said. */
function RepliesBlock({
  pr,
  author,
  reviews,
  replies,
}: {
  pr: PullRequestRef
  author: ConversationAuthor | null
  reviews: Array<ConversationReview>
  replies: Array<Reply>
}) {
  const latest = reviews.at(-1)!
  const threadCount = new Set(replies.map((reply) => reply.thread.id)).size
  return (
    <li className="relative pl-space-6">
      <Avatar author={author} size="md" className={TIMELINE_AVATAR} />
      <Byline
        author={author}
        createdAt={latest.created_at}
        href={latest.html_url}
        verb={`replied in ${plural(threadCount, "thread")}`}
      />
      <ul className="mt-space-1 flex flex-col gap-space-1">
        {replies.map((reply) => (
          <li key={reply.comment.id}>
            <ReplyRow pr={pr} reply={reply} />
          </li>
        ))}
      </ul>
    </li>
  )
}

function ReplyRow({ pr, reply }: { pr: PullRequestRef; reply: Reply }) {
  const [open, setOpen] = useState(false)
  const { thread, comment } = reply
  if (open)
    return (
      <ThreadCard
        pr={pr}
        thread={thread}
        withContext
        onCollapse={() => setOpen(false)}
      />
    )
  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      title={thread.path}
      className="flex w-full min-w-0 items-baseline gap-space-2 rounded-md px-space-1 py-space-1 text-left text-xs hover:bg-surface-level-1-hover"
    >
      {/* Truncates from the left so the line number, which tells rows apart, stays. */}
      <span className="max-w-[45%] shrink-0 truncate text-left font-mono text-xxs text-secondary [direction:rtl]">
        <bdi>{threadLocation(thread)}</bdi>
      </span>
      <span className="min-w-0 flex-1 truncate text-primary">
        {plainFirstLine(comment.body)}
      </span>
      {thread.resolved && (
        <CheckIcon
          aria-label="Resolved"
          className="size-3 shrink-0 self-center text-secondary"
        />
      )}
    </button>
  )
}

function ItemBlock({
  pr,
  item,
  earlier,
  parts,
}: {
  pr: PullRequestRef
  item: Said
  earlier: Array<Said>
  parts: ReviewParts
}) {
  const bot = Boolean(item.author?.bot)
  const body = item.body.trim()
  const [open, setOpen] = useState(!bot)
  const [showEarlier, setShowEarlier] = useState(false)
  const { started, replies } = parts
  const leftOnlyComments = item.kind === "review" && !body && started.length > 0
  const verb =
    item.kind === "review"
      ? leftOnlyComments && item.state === "COMMENTED"
        ? `left ${plural(started.length, "comment")}`
        : reviewStateWords[item.state]
      : "commented"
  return (
    <li className="relative pl-space-6">
      <Avatar author={item.author} size="md" className={TIMELINE_AVATAR} />
      <div className="flex min-w-0 items-center gap-space-2">
        {item.kind === "review" && item.state !== "COMMENTED" && (
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
            className="shrink-0 rounded-sm px-space-1 text-xxs text-secondary hover:bg-surface-level-1-hover hover:text-primary"
            title="Said the same thing before"
          >
            ×{earlier.length + 1}
          </button>
        )}
      </div>
      {showEarlier && (
        <ul className="mt-space-1 flex flex-col gap-0.5 text-xxs text-secondary">
          {earlier.map((said) => (
            <li key={said.id}>
              <a
                href={said.html_url}
                target="_blank"
                rel="noreferrer"
                className="hover:text-primary hover:underline"
              >
                Said the same {formatWhen(said.created_at)}
              </a>
            </li>
          ))}
        </ul>
      )}
      {body &&
        (open ? (
          <div className="mt-space-1 rounded-lg border border-default bg-surface-level-1 px-space-3 py-space-2 text-xs leading-[1.6]">
            <Markdown content={item.body} />
            {bot && (
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="mt-space-1 text-xxs text-secondary hover:text-primary"
              >
                Fold
              </button>
            )}
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="mt-0.5 block w-full truncate text-left text-xs text-secondary hover:text-primary"
          >
            {plainFirstLine(item.body)}
          </button>
        ))}
      {started.length > 0 && (
        <ul className="mt-space-1 flex flex-col gap-space-1">
          {started.map((thread) => (
            <li key={thread.id}>
              <ThreadRow pr={pr} thread={thread} />
            </li>
          ))}
        </ul>
      )}
      {replies.length > 0 && (
        <ul className="mt-space-1 flex flex-col gap-space-1">
          {replies.map((reply) => (
            <li key={reply.comment.id}>
              <ReplyRow pr={pr} reply={reply} />
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
    mutationFn: (text: string) => postReviewConversationComment(pr, text),
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
    <div className="border-t border-default p-space-3">
      <Textarea
        size="sm"
        aria-label="Comment on this pull request"
        placeholder="Comment on this pull request"
        rows={2}
        autoResize
        maxHeight={192}
        value={body}
        onChange={setBody}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault()
            send()
          }
        }}
      />
      {post.error && (
        <p role="alert" className="mt-space-1 text-xs text-error-secondary">
          {post.error.message}
        </p>
      )}
      <div className="mt-space-2 flex justify-end">
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
