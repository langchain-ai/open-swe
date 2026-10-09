import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useMemo } from "react"
import { ArrowSquareOutIcon, XIcon } from "@phosphor-icons/react"

import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { ReviewChatActionsContext } from "@/features/reviews/components/ReviewChatActions"
import type { DiffRange } from "@/features/reviews/lib/chatDiffActions"
import { useReviewChat } from "@/features/reviews/lib/reviewKeys"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useShortcutLabel } from "@/lib/hotkeys"
import { AgentMark } from "./AgentMark"
import { Discussion } from "./Discussion"
import { openFindingCounts } from "./findings"
import {
  reviewQueries,
  useOpenConversations,
  type PullRequestRef,
} from "./queries"
import { useReviewPage } from "./store"
import { plural } from "./text"

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
  const chat = useReviewChat(pr).data
  const open = useOpenConversations(pr)?.threads.length ?? 0

  return (
    <Tabs
      value={tab}
      onValueChange={(value) => {
        if (value === "chat" || value === "discussion") setRailTab(value)
      }}
      render={<aside />}
      data-review-rail
      aria-label="Chat and discussion"
      className="h-full min-h-0 gap-0 bg-background"
    >
      <div className="flex h-11 shrink-0 items-center gap-1 border-b border-border px-2">
        <TabsList variant="line">
          <TabsTrigger value="chat">
            <AgentMark />
            Chat
          </TabsTrigger>
          <TabsTrigger value="discussion">
            Discussion
            {open > 0 && (
              <span
                title={plural(open, "open conversation")}
                className="rounded-full bg-muted px-1.5 text-[10px] text-muted-foreground tabular-nums"
              >
                {open}
              </span>
            )}
          </TabsTrigger>
        </TabsList>
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
      <TabsContent value="chat" keepMounted className="min-h-0">
        <ReviewChatPanel pr={pr} />
      </TabsContent>
      <TabsContent value="discussion" keepMounted className="min-h-0">
        <Discussion pr={pr} />
      </TabsContent>
    </Tabs>
  )
}

/** Ways into an empty chat, picked from where this PR stands. They fill the composer; nothing sends unseen. */
function ChatStarters({ pr }: { pr: PullRequestRef }) {
  const detail = useQuery(reviewQueries.detail(pr)).data
  const status = useQuery(reviewQueries.status(pr)).data
  const open = useOpenConversations(pr)?.threads.length ?? 0
  const askInChat = useReviewPage((state) => state.askInChat)
  const askShortcut = useShortcutLabel("mod+l")
  const bugs = detail ? openFindingCounts(detail.findings).bugs : 0
  const starters = [
    "Walk me through this pull request: what changed, and why?",
    status?.ci === "failing"
      ? "Why are the checks failing, and what would fix them?"
      : null,
    bugs > 0
      ? "Which of Open SWE's findings matter most, and are any false alarms?"
      : null,
    open > 0
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
        Or select code in the diff and press {askShortcut} to ask about those
        lines.
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
