import { CaretRightIcon, CopyIcon } from "@phosphor-icons/react"

import type { ReviewFinding } from "@/lib/api"
import type { ReviewThread } from "@/features/reviews/lib/conversationApi"
import { copyText } from "@/features/reviews/lib/copyText"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { AgentMark } from "@/features/reviews/page/AgentMark"
import {
  askAboutFinding,
  findingGroupColor,
  findingGroupLabel,
  findingGroupTextColor,
  fixFinding,
} from "@/features/reviews/page/findings"
import { InlineCode } from "@/features/reviews/page/inlineCode"
import type { PullRequestRef } from "@/features/reviews/page/queries"
import { useReviewPage } from "@/features/reviews/page/store"
import { plural } from "@/features/reviews/page/text"
import { useThreadActions } from "@/features/reviews/page/useThreadActions"
import { NoteFrame } from "./NoteFrame"
import { ReplyBox } from "./ReplyBox"
import { CommentRow, ResolveButton } from "./ThreadNote"

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
          <span
            className="shrink-0 font-medium"
            style={{ color: findingGroupTextColor[finding.group] }}
          >
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
              {plural(thread.comments.length - 1, "reply", "replies")}
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
                  copyText(
                    `**${finding.title}**\n\n${findingMarkdown(finding)}`,
                    "Copied the finding"
                  )
                }
              >
                <CopyIcon /> Copy
              </Button>
              {finding.github_review_comment_id !== null && (
                <a
                  className="ml-auto text-muted-foreground hover:text-foreground hover:underline"
                  href={githubUrls.pullRequest(
                    pr,
                    `#discussion_r${finding.github_review_comment_id}`
                  )}
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
  return (
    <div className="mt-2.5 flex flex-col gap-2 border-t border-border/70 pt-2.5">
      {thread.comments.slice(1).map((comment) => (
        <CommentRow key={comment.id} comment={comment} />
      ))}
      <ReplyBox reply={reply} placeholder="Reply on GitHub…" />
      <div className="flex items-center gap-1">
        <ResolveButton thread={thread} resolve={resolve} />
      </div>
    </div>
  )
}
