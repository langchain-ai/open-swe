import { useState } from "react"
import { CaretRightIcon, CheckIcon } from "@phosphor-icons/react"

import type { ReviewThread } from "@/features/reviews/lib/conversationApi"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useReviewPage } from "../store"
import type { PullRequestRef } from "../queries"
import { plainFirstLine } from "../text"
import { useThreadActions } from "../useThreadActions"
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
  const askInChat = useReviewPage((state) => state.askInChat)
  const { reply, resolve } = useThreadActions(pr, thread)
  const first = thread.comments[0]
  if (!first) return null

  if (!open)
    return (
      <NoteFrame className="py-1">
        <button
          type="button"
          onClick={() => setOpen(true)}
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
          <span className="shrink-0 font-medium">
            {first.author?.login.replace(/\[bot\]$/, "") ?? "ghost"}
          </span>
          <span className="min-w-0 flex-1 truncate text-muted-foreground">
            {plainFirstLine(first.body)}
          </span>
          {thread.comments.length > 1 && (
            <span className="shrink-0 text-muted-foreground tabular-nums">
              {thread.comments.length}
            </span>
          )}
          {thread.resolved && (
            <span className="flex shrink-0 items-center gap-1 text-muted-foreground">
              <CheckIcon className="size-3" /> Resolved
            </span>
          )}
        </button>
      </NoteFrame>
    )

  return (
    <NoteFrame>
      <div
        className={cn(
          "overflow-hidden rounded-lg border border-border bg-card text-xs shadow-xs",
          thread.resolved && "opacity-80"
        )}
      >
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
                  if (next) setOpen(false)
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
              onClick={() => setOpen(false)}
              className="ml-auto text-muted-foreground hover:text-foreground"
            >
              Collapse
            </button>
          </div>
        </div>
      </div>
    </NoteFrame>
  )
}
