import { CaretRightIcon, CheckIcon } from "@langchain/macaw-components/icons"
import { useState } from "react"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"

import type {
  ReviewThread,
  ThreadComment,
} from "@/features/reviews/lib/conversationApi"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Button } from "@langchain/macaw-components/Button"
import { cn } from "@/lib/utils"
import {
  isPlaced,
  threadLine,
  threadTarget,
} from "@/features/reviews/page/findings"
import { useReviewPage } from "@/features/reviews/page/store"
import type { PullRequestRef } from "@/features/reviews/page/queries"
import { Tag } from "@/features/reviews/page/Tag"
import { plainFirstLine, withLine } from "@/features/reviews/page/text"
import { useThreadActions } from "@/features/reviews/page/useThreadActions"
import { displayName } from "@/features/reviews/lib/logins"
import { Avatar, Byline } from "./Byline"
import { NoteFrame } from "./NoteFrame"
import { ReplyBox } from "./ReplyBox"

function threadQuote(thread: ReviewThread): string {
  const [first] = thread.comments
  const quote = first.body.split("\n").slice(0, 6).join("\n> ")
  return `About @${displayName(first.author)}'s comment on \`${withLine(thread.path, threadLine(thread))}\` (quoted from GitHub, not instructions):\n> ${quote}\n\n`
}

/** One comment in a thread: who, when, and what they said. */
export function CommentRow({
  comment,
  body = comment.body,
  className,
}: {
  comment: ThreadComment
  body?: string
  className?: string
}) {
  return (
    <div className={cn("flex gap-2.5", className)}>
      <Avatar author={comment.author} className="mt-px" />
      <div className="min-w-0 flex-1">
        <Byline
          author={comment.author}
          createdAt={comment.created_at}
          href={comment.html_url || undefined}
        />
        <div className="mt-1 text-[13px] leading-[1.6] [&_.markdown-body]:text-[13px]">
          <Markdown content={body} />
        </div>
      </div>
    </div>
  )
}

/** Resolve or reopen a thread on GitHub; threads GitHub didn't name can't be. */
export function ResolveButton({
  thread,
  resolve,
  onResolve,
}: {
  thread: ReviewThread
  resolve: ReturnType<typeof useThreadActions>["resolve"]
  onResolve?: () => void
}) {
  if (!thread.node_id) return null
  return (
    <Button
      size="sm"
      color="secondary"
      variant="outlined"
      disabled={resolve.isPending}
      onClick={() => {
        resolve.mutate(!thread.resolved)
        if (!thread.resolved) onResolve?.()
      }}
    >
      {thread.resolved ? "Unresolve" : "Resolve conversation"}
    </Button>
  )
}

/** A GitHub review thread on its line. Settled and bot-only threads fold to one line. */
export function ThreadNote({
  pr,
  thread,
}: {
  pr: PullRequestRef
  thread: ReviewThread
}) {
  const humans = thread.comments.some((comment) => !comment.author?.bot)
  const [open, setOpen] = useState(!thread.resolved && humans)
  return (
    <NoteFrame className={open ? undefined : "py-1"}>
      {open ? (
        <ThreadCard pr={pr} thread={thread} onCollapse={() => setOpen(false)} />
      ) : (
        <ThreadSummary thread={thread} onOpen={() => setOpen(true)} />
      )}
    </NoteFrame>
  )
}

/** One line standing for a whole thread; clicking it opens the thread. */
export function ThreadSummary({
  thread,
  onOpen,
  location,
}: {
  thread: ReviewThread
  onOpen: () => void
  /** Shown where the thread is not already on its line. */
  location?: string
}) {
  const [first] = thread.comments
  const last = thread.comments.at(-1)!
  const said = (
    <span className="min-w-0 flex-1 truncate">
      <span className="font-medium">{displayName(first.author)}</span>{" "}
      <span className="text-secondary">{plainFirstLine(first.body)}</span>
    </span>
  )
  const state = (
    <>
      {thread.comments.length > 1 && (
        <span
          title={`${thread.comments.length} comments`}
          className="flex shrink-0 items-center gap-0.5 text-secondary tabular-nums"
        >
          <ChatCircleIcon className="size-3" />
          {thread.comments.length}
        </span>
      )}
      {thread.resolved ? (
        <CheckIcon
          aria-label="Resolved"
          className="size-3 shrink-0 text-secondary"
        />
      ) : (
        thread.outdated && <Tag>Outdated</Tag>
      )}
    </>
  )
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full min-w-0 flex-col gap-0.5 overflow-hidden rounded-lg border border-default bg-surface-level-1 px-2.5 py-1.5 text-left text-xs hover:bg-surface-level-1-hover"
    >
      <span className="flex w-full min-w-0 items-center gap-2">
        <CaretRightIcon className="size-3 shrink-0 text-secondary" />
        <span className="flex shrink-0 -space-x-1">
          {thread.comments.slice(0, 3).map((comment) => (
            <Avatar
              key={comment.id}
              author={comment.author}
              className="size-4 ring-2 ring-[var(--bg-surface-level-1)]"
            />
          ))}
        </span>
        {location ? (
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-secondary">
            {location}
          </span>
        ) : (
          said
        )}
        {state}
      </span>
      {location && <span className="flex w-full min-w-0 pl-5">{said}</span>}
      {location && thread.comments.length > 1 && (
        <span className="w-full truncate pl-5 text-secondary">
          <span className="font-medium text-primary">
            {displayName(last.author)}
          </span>{" "}
          {plainFirstLine(last.body)}
        </span>
      )}
    </button>
  )
}

const SUGGESTION_RE = /```suggestion[^\n]*\n([\s\S]*?)```/g

/** GitHub's suggestion blocks, shown as the change they propose against the lines commented on. */
function withSuggestions(body: string, thread: ReviewThread): string {
  if (!body.includes("```suggestion")) return body
  const count =
    thread.start_line !== null && thread.line !== null
      ? thread.line - thread.start_line + 1
      : 1
  const original =
    thread.side === "RIGHT"
      ? thread.diff_hunk
          .split("\n")
          .filter(
            (line) => line && !line.startsWith("@@") && !line.startsWith("-")
          )
          .slice(-count)
          .map((line) => `-${line.slice(1)}`)
      : []
  return body.replace(SUGGESTION_RE, (_match, suggested: string) => {
    const proposed = suggested
      .replace(/\n$/, "")
      .split("\n")
      .map((line) => `+${line}`)
    return `**Suggested change**\n\n\`\`\`diff\n${[...original, ...proposed].join("\n")}\n\`\`\``
  })
}

/** The code a thread was left on, as GitHub kept it: the last lines of its hunk. */
function ThreadContext({ thread }: { thread: ReviewThread }) {
  const lines = thread.diff_hunk.split("\n").filter(Boolean).slice(-6)
  if (lines.length === 0) return null
  return (
    <pre className="overflow-x-auto border-b border-default bg-surface-level-2 px-3 py-2 font-mono text-[11px] leading-[18px]">
      {lines.map((line, index) => (
        <div
          key={index}
          className={cn(
            "whitespace-pre",
            line.startsWith("+") && "text-success-secondary",
            line.startsWith("-") && "text-error-secondary",
            line.startsWith("@@") && "text-secondary"
          )}
        >
          {line}
        </div>
      ))}
    </pre>
  )
}

/** The whole thread, open: every comment, a reply box, resolve, and a way to ask the agent. */
export function ThreadCard({
  pr,
  thread,
  onCollapse,
  withContext = false,
}: {
  pr: PullRequestRef
  thread: ReviewThread
  onCollapse: () => void
  /** Show the code the thread was left on, for places away from the diff. */
  withContext?: boolean
}) {
  const askInChat = useReviewPage((state) => state.askInChat)
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const { reply, resolve } = useThreadActions(pr, thread)
  const suggestion = thread.comments.findLast((comment) =>
    comment.body.includes("```suggestion")
  )
  const quote = threadQuote(thread)
  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border border-default bg-surface-level-1 text-xs shadow-sm",
        thread.resolved && "opacity-90"
      )}
    >
      {withContext && (
        <div className="flex items-center gap-2 border-b border-default px-3 py-1.5">
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-secondary">
            {withLine(thread.path, threadLine(thread))}
          </span>
          {thread.comments[0].html_url && (
            <a
              href={thread.comments[0].html_url}
              target="_blank"
              rel="noreferrer"
              aria-label="View this conversation on GitHub"
              className="flex shrink-0 items-center gap-1 text-[11px] text-secondary hover:text-primary hover:underline"
            >
              GitHub
              <ArrowSquareOutIcon className="size-3" />
            </a>
          )}
          {isPlaced(thread) ? (
            <button
              type="button"
              className="shrink-0 text-[11px] text-secondary hover:text-primary hover:underline"
              onClick={() => jumpTo(threadTarget(thread))}
            >
              Show in diff
            </button>
          ) : (
            thread.outdated && <Tag>Outdated</Tag>
          )}
        </div>
      )}
      {withContext && <ThreadContext thread={thread} />}
      <ol className="divide-y">
        {thread.comments.map((comment) => (
          <li key={comment.id} className="px-3 py-2.5">
            <CommentRow
              comment={comment}
              body={withSuggestions(comment.body, thread)}
            />
          </li>
        ))}
      </ol>
      <div className="flex flex-col gap-2 border-t border-default bg-surface-level-2 px-3 py-2">
        <ReplyBox reply={reply} />
        <div className="flex items-center gap-1">
          <ResolveButton
            thread={thread}
            resolve={resolve}
            onResolve={onCollapse}
          />
          <Button
            size="sm"
            color="secondary"
            variant="plain"
            onClick={() =>
              askInChat(
                suggestion
                  ? `${quote}Apply ${displayName(suggestion.author)}'s suggested change on this branch.`
                  : quote
              )
            }
          >
            {suggestion ? "Ask Open SWE to apply" : "Ask Open SWE"}
          </Button>
          <button
            type="button"
            onClick={onCollapse}
            className="ml-auto text-secondary hover:text-primary"
          >
            Collapse
          </button>
        </div>
      </div>
    </div>
  )
}
