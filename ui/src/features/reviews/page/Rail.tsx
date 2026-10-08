import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useMemo } from "react"
import { ArrowSquareOutIcon, XIcon } from "@phosphor-icons/react"

import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { ReviewChatActionsContext } from "@/features/reviews/components/ReviewChatActions"
import type { DiffRange } from "@/features/reviews/lib/chatDiffActions"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { AgentMark } from "./AgentMark"
import { Discussion } from "./Discussion"
import { openBugCount } from "./pullRequestStanding"
import { reviewQueries, useReviewChat, type PullRequestRef } from "./queries"
import { useReviewPage, type RailTab } from "./store"

/** The right column: the agent you can talk to about this PR, and the people who already did. */
export function Rail({
  pr,
  onClose,
}: {
  pr: PullRequestRef
  onClose?: () => void
}) {
  const tab = useReviewPage((state) => state.railTab)
  const setRailTab = useReviewPage((state) => state.setRailTab)
  const conversation = useQuery(reviewQueries.conversation(pr)).data
  const chat = useReviewChat(pr).data
  const unresolved = conversation?.threads.filter(
    (thread) => !thread.resolved
  ).length
  const tabs: ReadonlyArray<RailTab> = ["chat", "discussion"]
  const onTabKey = (event: React.KeyboardEvent) => {
    const step =
      event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0
    if (!step) return
    event.preventDefault()
    const next = tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length]!
    setRailTab(next)
    event.currentTarget
      .querySelector<HTMLElement>(`[data-rail-tab="${next}"]`)
      ?.focus()
  }

  return (
    <aside
      data-review-rail
      aria-label="Chat and discussion"
      className="flex h-full min-h-0 flex-col bg-background"
    >
      <div className="flex h-11 shrink-0 items-center gap-1 border-b border-border px-2">
        <div
          role="tablist"
          aria-label="Rail"
          onKeyDown={onTabKey}
          className="flex items-center gap-1"
        >
          <RailTabButton
            tab="chat"
            active={tab === "chat"}
            onSelect={setRailTab}
          >
            <AgentMark />
            Chat
          </RailTabButton>
          <RailTabButton
            tab="discussion"
            active={tab === "discussion"}
            onSelect={setRailTab}
          >
            Discussion
            {unresolved !== undefined && unresolved > 0 && (
              <span
                title={`${unresolved} open conversation${unresolved === 1 ? "" : "s"}`}
                className="rounded-full bg-muted px-1.5 text-[10px] text-muted-foreground tabular-nums"
              >
                {unresolved}
              </span>
            )}
          </RailTabButton>
        </div>
        <span className="flex-1" />
        {tab === "chat" && chat?.thread_id && (
          <Link
            to="/agents/$threadId"
            params={{ threadId: chat.thread_id }}
            className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
            title="Open this conversation as a full thread"
          >
            Full thread
            <ArrowSquareOutIcon className="size-3" />
          </Link>
        )}
        {onClose && (
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="flex size-6 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <XIcon className="size-3.5" />
          </button>
        )}
      </div>
      {/* Both stay mounted: the chat registers drafted comments the diff shows, and keeps its stream. */}
      <div className={cn("min-h-0 flex-1", tab !== "chat" && "hidden")}>
        <ReviewChatPanel pr={pr} />
      </div>
      <div className={cn("min-h-0 flex-1", tab !== "discussion" && "hidden")}>
        <Discussion pr={pr} />
      </div>
    </aside>
  )
}

function RailTabButton({
  tab,
  active,
  onSelect,
  children,
}: {
  tab: RailTab
  active: boolean
  onSelect: (tab: RailTab) => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      role="tab"
      data-rail-tab={tab}
      aria-selected={active}
      tabIndex={active ? 0 : -1}
      onClick={() => onSelect(tab)}
      className={cn(
        "flex h-7 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
        active && "bg-accent text-foreground"
      )}
    >
      {children}
    </button>
  )
}

/** Ways into an empty chat, picked from where this PR stands. They fill the composer; nothing sends unseen. */
function ChatStarters({ pr }: { pr: PullRequestRef }) {
  const detail = useQuery(reviewQueries.detail(pr)).data
  const status = useQuery(reviewQueries.status(pr)).data
  const askInChat = useReviewPage((state) => state.askInChat)
  const bugs = detail ? openBugCount(detail) : 0
  const starters = [
    "Walk me through this pull request: what changed, and why?",
    status?.ci === "failing"
      ? "Why are the checks failing, and what would fix them?"
      : null,
    bugs > 0
      ? "Which of Open SWE's findings matter most, and are any false alarms?"
      : null,
    status?.unresolvedThreads
      ? "Summarize the unresolved review comments and what each one asks for."
      : null,
    "What's the riskiest part of this change? Where should I look first?",
    "Is anything here untested?",
  ].filter((text): text is string => text !== null)
  return (
    <div className="w-full max-w-sm px-4">
      <p className="flex items-center gap-1.5 text-[13px] font-medium text-foreground">
        <AgentMark />
        Ask about this pull request
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Or select code in the diff and press ⌘L to ask about those lines.
      </p>
      <ul className="mt-3 flex flex-col gap-1.5">
        {starters.slice(0, 4).map((text) => (
          <li key={text}>
            <button
              type="button"
              onClick={() => askInChat(text)}
              className="w-full rounded-lg border border-border px-3 py-2 text-left text-xs text-foreground/90 transition-colors hover:border-ring/40 hover:bg-accent"
            >
              {text}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ReviewChatPanel({ pr }: { pr: PullRequestRef }) {
  const meta = useReviewChat(pr)
  const chatDraft = useReviewPage((state) => state.chatDraft)
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const excerpts = useReviewPage((state) => state.chatExcerpts)
  const removeExcerpt = useReviewPage((state) => state.removeChatExcerpt)
  const clearExcerpts = useReviewPage((state) => state.clearChatExcerpts)
  const actions = useMemo(
    () => ({
      owner: pr.owner,
      repo: pr.repo,
      number: pr.number,
      showInDiff: (range: DiffRange) =>
        jumpTo({
          kind: "line",
          path: range.file,
          line: range.endLine,
          side: range.side,
        }),
      emptyState: <ChatStarters pr={pr} />,
      excerpts,
      removeExcerpt,
      clearExcerpts,
    }),
    [pr, jumpTo, excerpts, removeExcerpt, clearExcerpts]
  )
  if (meta.isPending)
    return (
      <div className="flex h-full flex-col justify-end gap-3 p-4">
        <Skeleton className="h-16 w-3/4" />
        <Skeleton className="ml-auto h-10 w-1/2" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  if (meta.isError || !meta.data.available)
    return (
      <p className="p-4 text-xs text-muted-foreground">
        Chat is unavailable right now. Reload the page to try again.
      </p>
    )
  return (
    <ReviewChatActionsContext.Provider value={actions}>
      <div className="flex h-full min-h-0 flex-col [&>*]:min-h-0 [&>*]:flex-1">
        <AgentThreadPage
          threadId={meta.data.thread_id}
          composerDraft={chatDraft}
        />
      </div>
    </ReviewChatActionsContext.Provider>
  )
}
