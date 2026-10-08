import { useState } from "react"
import { CaretRightIcon, CopyIcon } from "@phosphor-icons/react"
import { toast } from "sonner"

import type { ReviewFinding } from "@/lib/api"
import type { ReviewThread } from "@/features/reviews/lib/conversationApi"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { AgentMark } from "../AgentMark"
import {
  askAboutFinding,
  findingGroupColor,
  findingGroupLabel,
  fixFinding,
} from "../findings"
import { InlineCode } from "../inlineCode"
import type { PullRequestRef } from "../queries"
import { useReviewPage } from "../store"
import { useThreadActions } from "../useThreadActions"
import { Avatar, Byline } from "./Byline"
import { NoteFrame } from "./NoteFrame"
import { ReplyBox } from "./ReplyBox"

function findingMarkdown(finding: ReviewFinding): string {
  return finding.suggestion
    ? `${finding.description}\n\n\`\`\`suggestion\n${finding.suggestion}\n\`\`\``
    : finding.description
}

/**
 * Open SWE's finding as a margin note: a severity rule and the agent's mark,
 * never a comment bubble, so it reads as the agent's and not a person's.
 */
export function FindingNote({
  pr,
  finding,
  thread,
}: {
  pr: PullRequestRef
  finding: ReviewFinding
  thread: ReviewThread | null
}) {
  const expandedFinding = useReviewPage((state) => state.expandedFinding)
  const setExpandedFinding = useReviewPage((state) => state.setExpandedFinding)
  const askInChat = useReviewPage((state) => state.askInChat)
  const open = expandedFinding === finding.id
  const settled = finding.status !== "open"
  const color = findingGroupColor[finding.group]
  return (
    <NoteFrame>
      <div
        className={cn(
          "rounded-r-lg border-y border-r border-border bg-[color-mix(in_oklab,var(--card)_92%,var(--primary))] text-xs",
          settled && "opacity-60"
        )}
        style={{ borderLeft: `3px solid ${color}` }}
      >
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setExpandedFinding(open ? null : finding.id)}
          className="flex w-full items-start gap-2 px-3 py-2 text-left"
        >
          <AgentMark className="mt-0.5" />
          <span className="shrink-0 font-medium" style={{ color }}>
            {findingGroupLabel[finding.group]}
          </span>
          <span
            className={cn(
              "min-w-0 flex-1 leading-5 text-foreground",
              settled && "line-through"
            )}
          >
            <InlineCode text={finding.title} />
          </span>
          {settled && (
            <span className="shrink-0 text-muted-foreground capitalize">
              {finding.status}
            </span>
          )}
          {thread && thread.comments.length > 1 && (
            <span className="shrink-0 text-muted-foreground tabular-nums">
              {thread.comments.length - 1} repl
              {thread.comments.length === 2 ? "y" : "ies"}
            </span>
          )}
          <CaretRightIcon
            className={cn(
              "mt-1 size-3 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-90"
            )}
          />
        </button>
        {open && (
          <div className="border-t border-border/70 px-3 pt-2 pb-2.5">
            <div className="text-[13px] leading-[1.6]">
              <Markdown content={findingMarkdown(finding)} />
            </div>
            {finding.resolution_note && (
              <p className="mt-2 text-muted-foreground">
                {finding.resolution_note}
              </p>
            )}
            {thread && <FindingReplies pr={pr} thread={thread} />}
            <div className="mt-2 flex flex-wrap items-center gap-1">
              <Button
                size="sm"
                variant="outline"
                onClick={() => askInChat(askAboutFinding(finding))}
              >
                Ask Open SWE
              </Button>
              {!settled && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => askInChat(fixFinding(finding))}
                >
                  Fix it
                </Button>
              )}
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  void navigator.clipboard
                    .writeText(
                      `**${finding.title}**\n\n${findingMarkdown(finding)}`
                    )
                    .then(() => toast.success("Copied the finding"))
                }
              >
                <CopyIcon /> Copy
              </Button>
              {finding.github_review_comment_id !== null && (
                <a
                  className="ml-auto text-muted-foreground hover:text-foreground hover:underline"
                  href={`https://github.com/${pr.owner}/${pr.repo}/pull/${pr.number}#discussion_r${finding.github_review_comment_id}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  On GitHub
                </a>
              )}
            </div>
          </div>
        )}
      </div>
    </NoteFrame>
  )
}

function FindingReplies({
  pr,
  thread,
}: {
  pr: PullRequestRef
  thread: ReviewThread
}) {
  const { reply, resolve } = useThreadActions(pr, thread)
  const [showReply, setShowReply] = useState(false)
  const replies = thread.comments.slice(1)
  return (
    <div className="mt-2.5 flex flex-col gap-2 border-t border-border/70 pt-2.5">
      {replies.map((comment) => (
        <div key={comment.id} className="flex gap-2">
          <Avatar author={comment.author} className="mt-px size-4" />
          <div className="min-w-0 flex-1">
            <Byline
              author={comment.author}
              createdAt={comment.created_at}
              href={comment.html_url || undefined}
            />
            <div className="mt-0.5 text-[13px] leading-[1.6]">
              <Markdown content={comment.body} />
            </div>
          </div>
        </div>
      ))}
      {showReply || replies.length > 0 ? (
        <ReplyBox
          pending={reply.isPending}
          onSend={(body) => reply.mutate(body)}
          placeholder="Reply on GitHub…"
        />
      ) : (
        <button
          type="button"
          onClick={() => setShowReply(true)}
          className="self-start text-muted-foreground hover:text-foreground"
        >
          Reply on GitHub
        </button>
      )}
      {thread.node_id && (
        <button
          type="button"
          disabled={resolve.isPending}
          onClick={() => resolve.mutate(!thread.resolved)}
          className="self-start text-muted-foreground hover:text-foreground"
        >
          {thread.resolved
            ? "Unresolve the conversation"
            : "Resolve the conversation"}
        </button>
      )}
    </div>
  )
}
