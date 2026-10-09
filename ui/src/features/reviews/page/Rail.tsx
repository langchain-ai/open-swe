import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import {
  TabGroup,
  TabLabel,
  TabList,
  TabPanel,
  TabPanels,
} from "@langchain/macaw-components/Tabs"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useMemo } from "react"

import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { ReviewChatActionsContext } from "@/features/reviews/components/ReviewChatActions"
import type { DiffRange } from "@/features/reviews/lib/chatDiffActions"
import { useReviewChat } from "@/features/reviews/lib/reviewKeys"
import { useShortcutLabel } from "@/lib/hotkeys"
import { AgentMark } from "./AgentMark"
import { Discussion } from "./Discussion"
import { openFindingCounts } from "./findings"
import {
  reviewQueries,
  useOpenConversations,
  useReviewStatus,
  type PullRequestRef,
} from "./queries"
import { useReviewPage, type RailTab } from "./store"

const TABS: ReadonlyArray<RailTab> = ["chat", "discussion"]

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
    <TabGroup
      as="aside"
      selectedIndex={TABS.indexOf(tab)}
      onChange={(index: number) => setRailTab(TABS[index] ?? "chat")}
      data-review-rail
      aria-label="Chat and discussion"
      className="flex h-full min-h-0 flex-col bg-surface-level-1"
    >
      <div className="flex h-11 shrink-0 items-center gap-space-2 border-b border-default px-space-3">
        <TabList className="self-stretch border-0">
          <TabLabel label="Chat" icon={AgentMark} className="pb-0" />
          <TabLabel
            label="Discussion"
            className="pb-0"
            badgeProps={
              open > 0
                ? { children: String(open), color: "secondary" }
                : undefined
            }
          />
        </TabList>
        <span className="flex-1" />
        {tab === "chat" && chat?.thread_id && (
          <Button
            size="xs"
            color="secondary"
            variant="plain"
            rightDecorator={ArrowSquareOutIcon}
            title="Open this conversation as a full thread"
            as={
              <Link
                to="/agents/$threadId"
                params={{ threadId: chat.thread_id }}
              />
            }
          >
            Full thread
          </Button>
        )}
        {onClose && (
          <IconButton
            icon={XIcon}
            label="Close"
            size="sm"
            color="secondary"
            variant="plain"
            onClick={onClose}
          />
        )}
      </div>
      {/* Both stay mounted: the chat registers drafted comments the diff shows, and keeps its stream. */}
      <TabPanels className="flex min-h-0 flex-1 flex-col">
        <TabPanel unmount={false} className="min-h-0 flex-1">
          <ReviewChatPanel pr={pr} />
        </TabPanel>
        <TabPanel unmount={false} className="min-h-0 flex-1">
          <Discussion pr={pr} />
        </TabPanel>
      </TabPanels>
    </TabGroup>
  )
}

/** Ways into an empty chat, picked from where this PR stands. They fill the composer; nothing sends unseen. */
function ChatStarters({ pr }: { pr: PullRequestRef }) {
  const detail = useQuery(reviewQueries.detail(pr)).data
  const status = useReviewStatus(pr).data
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
      <p className="flex items-center gap-1.5 text-[13px] font-medium text-primary">
        <AgentMark />
        Ask about this pull request
      </p>
      <p className="mt-1 text-xs text-secondary">
        Or select code in the diff and press {askShortcut} to ask about those
        lines.
      </p>
      <ul className="mt-3 flex flex-col gap-1.5">
        {starters.slice(0, 4).map((text) => (
          <li key={text}>
            <button
              type="button"
              onClick={() => askInChat(text)}
              className="w-full rounded-lg border border-default px-3 py-2 text-left text-xs text-primary transition-colors hover:border-strong hover:bg-surface-level-1-hover"
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
  const question = useReviewPage((state) => state.chatQuestion)
  const clearQuestion = useReviewPage((state) => state.clearChatQuestion)
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
      question,
      clearQuestion,
    }),
    [
      pr,
      jumpTo,
      excerpts,
      removeExcerpt,
      clearExcerpts,
      question,
      clearQuestion,
    ]
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
      <p className="p-4 text-xs text-secondary">
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
