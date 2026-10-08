import { useState } from "react"
import { CaretRightIcon, CheckIcon } from "@phosphor-icons/react"

import type { ReviewThread } from "@/features/reviews/lib/conversationApi"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useReviewPage } from "@/features/reviews/page/store"
import type { PullRequestRef } from "@/features/reviews/page/queries"
import { plainFirstLine } from "@/features/reviews/page/text"
import { useThreadActions } from "@/features/reviews/page/useThreadActions"
import { Avatar, Byline } from "./Byline"
import { NoteFrame } from "./NoteFrame"
import { ReplyBox } from "./ReplyBox"

export function threadQuote(thread: ReviewThread): string {
  const first = thread.comments[0]
  const line = thread.line ?? thread.original_line
  return `About @${first?.author?.login ?? "someone"}'s comment on \`${thread.path}${line ? `:${line}` : ""}\`:\n> ${(first?.body ?? "").split("\n").slice(0, 6).join("\n> ")}\n\n`
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
  const first = thread.comments[0]
  if (!first) return null
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
  const first = thread.comments[0]
  if (!first) return null
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-left text-xs hover:bg-accent"
    >
      <CaretRightIcon className="size-3 shrink-0 text-muted-foreground" />
      <span className="flex -space-x-1">
        {thread.comments.slice(0, 3).map((comment) => (
          <Avatar
            key={comment.id}
            author={comment.author}
            className="size-4 ring-2 ring-card"
          />
        ))}
      </span>
      {location && (
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
          {location}
        </span>
      )}
      <span className="min-w-0 flex-1 truncate">
        <span className="font-medium">
          {first.author?.login.replace(/\[bot\]$/, "") ?? "ghost"}
        </span>{" "}
        <span className="text-muted-foreground">
          {plainFirstLine(first.body)}
        </span>
      </span>
      {thread.comments.length > 1 && (
        <span className="shrink-0 text-muted-foreground tabular-nums">
          {thread.comments.length}
        </span>
      )}
      {thread.outdated && (
        <span className="shrink-0 rounded-[4px] border border-border px-1 text-[10px] text-muted-foreground">
          Outdated
        </span>
      )}
      {thread.resolved && (
        <span className="flex shrink-0 items-center gap-1 text-muted-foreground">
          <CheckIcon className="size-3" /> Resolved
        </span>
      )}
    </button>
  )
}

/** The code a thread was left on, as GitHub kept it: the last lines of its hunk. */
function ThreadContext({ thread }: { thread: ReviewThread }) {
  const lines = thread.diff_hunk.split("\n").filter(Boolean).slice(-6)
  if (lines.length === 0) return null
  return (
    <pre className="overflow-x-auto border-b border-border bg-muted/40 px-3 py-2 font-mono text-[11px] leading-[18px]">
      {lines.map((line, index) => (
        <div
          key={index}
          className={cn(
            "whitespace-pre",
            line.startsWith("+") && "text-success-foreground",
            line.startsWith("-") && "text-destructive-foreground",
            line.startsWith("@@") && "text-muted-foreground"
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
  const line = thread.line ?? thread.original_line
  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border border-border bg-card text-xs shadow-xs",
        thread.resolved && "opacity-90"
      )}
    >
      {withContext && (
        <div className="flex items-center gap-2 border-b border-border px-3 py-1.5">
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
            {thread.path}
            {line ? `:${line}` : ""}
          </span>
          {thread.outdated ? (
            <span className="shrink-0 rounded-[4px] border border-border px-1 text-[10px] text-muted-foreground">
              Outdated
            </span>
          ) : (
            thread.line !== null && (
              <button
                type="button"
                className="shrink-0 text-[11px] text-muted-foreground hover:text-foreground hover:underline"
                onClick={() =>
                  thread.line !== null &&
                  jumpTo({
                    kind: "line",
                    path: thread.path,
                    line: thread.line,
                    side: thread.side,
                  })
                }
              >
                Show in diff
              </button>
            )
          )}
        </div>
      )}
      {withContext && <ThreadContext thread={thread} />}
      <ol className="divide-y divide-border">
        {thread.comments.map((comment) => (
          <li key={comment.id} className="flex gap-2.5 px-3 py-2.5">
            <Avatar author={comment.author} className="mt-px" />
            <div className="min-w-0 flex-1">
              <Byline
                author={comment.author}
                createdAt={comment.created_at}
                href={comment.html_url || undefined}
              />
              <div className="mt-1 text-[13px] leading-[1.6] [&_.markdown-body]:text-[13px]">
                <Markdown content={comment.body} />
              </div>
            </div>
          </li>
        ))}
      </ol>
      <div className="flex flex-col gap-2 border-t border-border bg-muted/40 px-3 py-2">
        <ReplyBox
          pending={reply.isPending}
          onSend={(body) => reply.mutate(body)}
        />
        <div className="flex items-center gap-1">
          {thread.node_id && (
            <Button
              size="sm"
              variant="outline"
              disabled={resolve.isPending}
              onClick={() => {
                const next = !thread.resolved
                resolve.mutate(next)
                if (next) onCollapse()
              }}
            >
              {thread.resolved ? "Unresolve" : "Resolve conversation"}
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => askInChat(threadQuote(thread))}
          >
            Ask Open SWE
          </Button>
          <button
            type="button"
            onClick={onCollapse}
            className="ml-auto text-muted-foreground hover:text-foreground"
          >
            Collapse
          </button>
        </div>
      </div>
    </div>
  )
}
