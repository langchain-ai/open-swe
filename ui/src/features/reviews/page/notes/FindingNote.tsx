import { Link as MacawLink } from "@langchain/macaw-components/Link"
import { CaretRightIcon } from "@langchain/macaw-components/icons"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"

import type { ReviewFinding } from "@/lib/api"
import type { ReviewThread } from "@/features/reviews/lib/conversationApi"
import { copyText } from "@/features/reviews/lib/copyText"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Button } from "@langchain/macaw-components/Button"
import { cn, INHERITED_LINK } from "@/lib/utils"
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
  const label = [
    findingGroupLabel[finding.group],
    finding.title,
    settled && finding.status,
    thread &&
      thread.comments.length > 1 &&
      plural(thread.comments.length - 1, "reply", "replies"),
  ]
    .filter(Boolean)
    .join(", ")
  return (
    <NoteFrame>
      <div
        className="rounded-r-lg border-y border-r border-default bg-brand-muted text-xs"
        style={{ borderLeft: `3px solid ${color}` }}
      >
        <Button
          color="secondary"
          variant="plain"
          size="sm"
          aria-label={label}
          type="button"
          aria-expanded={open}
          onClick={() => setExpandedFinding(open ? null : finding.id)}
          className="h-auto w-full items-start justify-start gap-space-2 px-space-3 py-space-2 text-left whitespace-normal"
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
              "min-w-0 flex-1 leading-5 text-primary",
              settled && "line-through"
            )}
          >
            <InlineCode text={finding.title} />
          </span>
          {settled && (
            <span className="shrink-0 text-secondary capitalize">
              {finding.status}
            </span>
          )}
          {thread && thread.comments.length > 1 && (
            <span className="shrink-0 text-secondary tabular-nums">
              {plural(thread.comments.length - 1, "reply", "replies")}
            </span>
          )}
          <CaretRightIcon
            className={cn(
              "mt-space-1 size-3 shrink-0 text-secondary transition-transform",
              open && "rotate-90"
            )}
          />
        </Button>
        {open && (
          <div className="border-t border-subtle px-space-3 pt-space-2 pb-space-2">
            <div className="text-xs leading-[1.6]">
              <Markdown content={findingMarkdown(finding)} />
            </div>
            {finding.resolution_note && (
              <p className="mt-space-2 text-secondary">
                {finding.resolution_note}
              </p>
            )}
            {thread && <FindingReplies pr={pr} thread={thread} />}
            <div className="mt-space-2 flex flex-wrap items-center gap-space-1">
              <Button
                size="sm"
                color="secondary"
                variant="outlined"
                onClick={() => askInChat(askAboutFinding(finding))}
              >
                Ask Open SWE
              </Button>
              {!settled && (
                <Button
                  size="sm"
                  color="secondary"
                  variant="plain"
                  onClick={() => askInChat(fixFinding(finding))}
                >
                  Fix it
                </Button>
              )}
              <Button
                size="sm"
                color="secondary"
                variant="plain"
                leftDecorator={CopyIcon}
                onClick={() =>
                  copyText(
                    `**${finding.title}**\n\n${findingMarkdown(finding)}`,
                    "Copied the finding"
                  )
                }
              >
                Copy
              </Button>
              {finding.github_review_comment_id !== null && (
                <MacawLink
                  variant="xs"
                  className={cn(
                    INHERITED_LINK,
                    "ml-auto text-secondary hover:text-primary hover:underline"
                  )}
                  href={githubUrls.pullRequest(
                    pr,
                    `#discussion_r${finding.github_review_comment_id}`
                  )}
                  target="_blank"
                  rel="noreferrer"
                >
                  On GitHub
                </MacawLink>
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
    <div className="mt-space-2 flex flex-col gap-space-2 border-t border-subtle pt-space-2">
      {thread.comments.slice(1).map((comment) => (
        <CommentRow key={comment.id} comment={comment} />
      ))}
      <ReplyBox
        threadId={thread.id}
        reply={reply}
        placeholder="Reply on GitHub…"
      />
      <div className="flex items-center gap-space-1">
        <ResolveButton thread={thread} resolve={resolve} />
      </div>
    </div>
  )
}
