import { Button } from "@langchain/macaw-components/Button"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Kbd } from "@langchain/macaw-components/Kbd"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { TrayIcon } from "@phosphor-icons/react/dist/ssr/Tray"
import { useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { useEffect, useMemo, useRef, useState } from "react"

import type { AppCommand } from "@/lib/appCommands"
import { useRegisterAppCommands } from "@/lib/appCommands"
import { useChatRoutes } from "@/lib/chatRoutes"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"
import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { compactAge } from "@/features/agents/components/SidebarThreadRow"
import {
  buildInbox,
  INBOX_SPLITS,
  inSplit,
  type InboxItem,
  type InboxSplit,
  type InboxTone,
  type ReviewInboxItem,
} from "@/features/agents/lib/inbox"
import {
  markAgentThreadViewed,
  useResolveAgentThread,
  useSidebarRecents,
} from "@/features/agents/lib/queries"
import { withoutNestedWorkers } from "@/features/agents/lib/sidebarThreads"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import { useOpenPullRequests } from "@/features/reviews/lib/useOpenPullRequests"

/**
 * A Superhuman-style triage view: only what is waiting on you, one item open
 * beside the list. J/K move, O opens the item full page, E marks it done.
 */
export function InboxPage() {
  const login = useSession().data?.login ?? ""
  const navigate = useNavigate()
  const chat = useChatRoutes()
  const queryClient = useQueryClient()
  const resolveThread = useResolveAgentThread().mutate
  const threads = useSidebarRecents({
    repoMode: false,
    owned: true,
    sort: "updated",
  })
  const reviews = useOpenPullRequests(
    login,
    [],
    "updatedAt",
    "desc",
    "review-assigned"
  )
  const [split, setSplit] = useState<InboxSplit>("all")
  const [doneReviews, setDoneReviews] = useState<ReadonlySet<string>>(
    () => new Set()
  )
  const [selection, setSelection] = useState<{ key?: string; index: number }>({
    index: 0,
  })
  const list = useRef<HTMLDivElement>(null)

  const reviewRows = useMemo(
    () => reviews.data?.pages.flatMap((page) => page.pullRequests) ?? [],
    [reviews.data]
  )
  const inbox = buildInbox({
    threads: withoutNestedWorkers(threads.items),
    reviews: reviewRows,
    done: doneReviews,
  })
  const items = inbox.filter((item) => inSplit(item, split))
  const foundIndex = items.findIndex((item) => item.key === selection.key)
  const index =
    foundIndex === -1
      ? Math.min(selection.index, Math.max(items.length - 1, 0))
      : foundIndex
  const selected = items[index]

  useEffect(() => {
    if (selected?.kind === "thread" && !selected.thread.viewed)
      markAgentThreadViewed(queryClient, selected.thread.id)
  }, [selected, queryClient])

  useEffect(() => {
    list.current
      ?.querySelector(`[data-inbox-key="${CSS.escape(selected?.key ?? "")}"]`)
      ?.scrollIntoView({ block: "nearest" })
  }, [selected?.key])

  const latest = useRef({ items, index, split })
  useEffect(() => {
    latest.current = { items, index, split }
  })

  const commands = useMemo<ReadonlyArray<AppCommand>>(() => {
    const move = (step: 1 | -1) => {
      const { items: current, index: at } = latest.current
      const nextIndex = Math.min(Math.max(at + step, 0), current.length - 1)
      const next = current[nextIndex]
      if (next) setSelection({ key: next.key, index: nextIndex })
    }
    const open = () => {
      const item = latest.current.items[latest.current.index]
      if (!item) return
      if (item.kind === "review") void navigate(reviewPageRoute(item.review))
      else
        void navigate({
          to: chat.thread,
          params: { threadId: item.thread.id },
        })
    }
    const done = () => {
      const { items: current, index: at } = latest.current
      const item = current[at]
      if (!item) return
      const neighbor = current[at + 1] ? at + 1 : at - 1
      setSelection({ key: current[neighbor]?.key, index: Math.max(at, 0) })
      if (item.kind === "review")
        setDoneReviews((keys) => new Set([...keys, item.key]))
      else resolveThread({ threadId: item.thread.id, resolved: true })
    }
    const cycleSplit = (step: 1 | -1) => {
      const at = INBOX_SPLITS.findIndex(
        (option) => option.value === latest.current.split
      )
      const next =
        INBOX_SPLITS[(at + step + INBOX_SPLITS.length) % INBOX_SPLITS.length]
      if (next) setSplit(next.value)
    }
    return [
      {
        id: "inbox-next",
        label: "Next inbox item",
        shortcuts: ["j"],
        group: "Inbox",
        run: () => move(1),
      },
      {
        id: "inbox-previous",
        label: "Previous inbox item",
        shortcuts: ["k"],
        group: "Inbox",
        run: () => move(-1),
      },
      {
        id: "inbox-open",
        label: "Open inbox item full page",
        shortcuts: ["o"],
        group: "Inbox",
        run: open,
      },
      {
        id: "inbox-done",
        label: "Mark done and go to next",
        aliases: ["archive", "done"],
        shortcuts: ["e"],
        group: "Inbox",
        run: done,
      },
      {
        id: "inbox-next-split",
        label: "Next inbox split",
        shortcuts: ["]"],
        group: "Inbox",
        run: () => cycleSplit(1),
      },
      {
        id: "inbox-previous-split",
        label: "Previous inbox split",
        shortcuts: ["["],
        group: "Inbox",
        run: () => cycleSplit(-1),
      },
    ]
  }, [chat.thread, navigate, resolveThread])
  useRegisterAppCommands(commands)

  const run = (id: string) =>
    commands.find((command) => command.id === id)?.run?.()
  const onListKeyDown = (event: React.KeyboardEvent) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return
    const action =
      event.key === "ArrowDown"
        ? "inbox-next"
        : event.key === "ArrowUp"
          ? "inbox-previous"
          : event.key === "Enter"
            ? "inbox-open"
            : event.key === "Tab"
              ? event.shiftKey
                ? "inbox-previous-split"
                : "inbox-next-split"
              : null
    if (!action) return
    event.preventDefault()
    void run(action)
  }

  const loading = threads.isPending || (Boolean(login) && reviews.isPending)

  return (
    <div className="flex h-full min-w-0 flex-1">
      <aside className="flex w-[380px] shrink-0 flex-col border-r border-default">
        <header className="flex flex-col gap-3 px-4 pt-5 pb-3">
          <div className="flex items-center gap-2">
            <TrayIcon size={18} weight="regular" />
            <h1 className="font-heading text-base font-medium text-primary">
              Inbox
            </h1>
            <span className="text-xs text-tertiary">{inbox.length}</span>
          </div>
          <GroupedTabs<InboxSplit>
            size="xs"
            className="w-fit"
            value={split}
            onChange={setSplit}
            options={INBOX_SPLITS.map(({ value, label }) => ({
              value,
              display: label,
            }))}
          />
        </header>
        <div
          ref={list}
          role="listbox"
          aria-label="Inbox"
          tabIndex={0}
          onKeyDown={onListKeyDown}
          className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 outline-none"
        >
          {loading && items.length === 0 ? (
            <InboxSkeleton />
          ) : items.length === 0 ? (
            <InboxZero />
          ) : (
            items.map((item, itemIndex) => (
              <InboxRow
                key={item.key}
                item={item}
                selected={item.key === selected?.key}
                onSelect={() => {
                  setSelection({ key: item.key, index: itemIndex })
                  list.current?.focus({ preventScroll: true })
                }}
              />
            ))
          )}
          {threads.hasMore && split !== "reviews" && (
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              className="mt-1 w-full"
              disabled={threads.isFetchingNextPage}
              onClick={threads.fetchNextPage}
            >
              {threads.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          )}
        </div>
        <footer className="flex flex-wrap gap-x-3 gap-y-1 border-t border-default px-4 py-2 text-xxs text-tertiary">
          <span>
            <Kbd>J</Kbd>/<Kbd>K</Kbd> move
          </span>
          <span>
            <Kbd>E</Kbd> done
          </span>
          <span>
            <Kbd>O</Kbd> open
          </span>
          <span>
            <Kbd>[</Kbd>/<Kbd>]</Kbd> split
          </span>
        </footer>
      </aside>
      <section className="relative flex min-w-0 flex-1 overflow-hidden">
        {selected?.kind === "thread" ? (
          <AgentThreadPage
            key={selected.thread.id}
            threadId={selected.thread.id}
          />
        ) : selected?.kind === "review" ? (
          <ReviewPreview item={selected} />
        ) : null}
      </section>
    </div>
  )
}

const TONE_DOT: Record<InboxTone, string> = {
  error: "bg-error-strong",
  attention: "bg-warning-strong",
  info: "bg-brand",
}

const TONE_TEXT: Record<InboxTone, string> = {
  error: "text-error-secondary",
  attention: "text-warning-secondary",
  info: "text-secondary",
}

function InboxRow({
  item,
  selected,
  onSelect,
}: {
  item: InboxItem
  selected: boolean
  onSelect: () => void
}) {
  const Icon = item.kind === "review" ? GitPullRequestIcon : ChatCircleIcon
  const unread = item.kind === "thread" && !item.thread.viewed
  return (
    <div
      role="option"
      aria-selected={selected}
      data-inbox-key={item.key}
      onClick={onSelect}
      className={cn(
        "mb-0.5 flex cursor-default gap-2.5 rounded-lg px-2.5 py-2 transition-colors",
        selected ? "bg-selected" : "hover:bg-surface-level-2-hover"
      )}
    >
      <Icon
        size={16}
        weight="regular"
        className="mt-0.5 shrink-0 text-icon-secondary"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-sm text-primary",
              unread && "font-medium"
            )}
          >
            {item.title}
          </span>
          <span className="shrink-0 text-xxs text-tertiary">
            {item.updatedAt ? compactAge(item.updatedAt) : ""}
          </span>
        </div>
        <div className="flex items-center gap-1.5 text-xs">
          {(unread || item.tone !== "info") && (
            <span
              className={cn(
                "size-1.5 shrink-0 rounded-full",
                TONE_DOT[item.tone]
              )}
            />
          )}
          <span className={cn("shrink-0", TONE_TEXT[item.tone])}>
            {item.reason}
          </span>
          <span className="min-w-0 truncate text-tertiary">
            · {item.subtitle}
          </span>
        </div>
      </div>
    </div>
  )
}

function ReviewPreview({ item }: { item: ReviewInboxItem }) {
  return (
    <div className="m-auto flex max-w-md flex-col items-center gap-3 p-6 text-center">
      <GitPullRequestIcon
        size={28}
        weight="regular"
        className="text-icon-secondary"
      />
      <h2 className="text-base font-medium text-primary">{item.title}</h2>
      <p className="text-sm text-secondary">
        {item.subtitle} · your review was requested
      </p>
      <Button as={<Link {...reviewPageRoute(item.review)} />} size="sm">
        Open review
      </Button>
      <p className="text-xs text-tertiary">
        Press <Kbd>O</Kbd> to open, <Kbd>E</Kbd> to mark done.
      </p>
    </div>
  )
}

function InboxZero() {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-16 text-center">
      <TrayIcon size={28} weight="regular" className="text-icon-tertiary" />
      <p className="text-sm font-medium text-primary">You're all caught up</p>
      <p className="text-xs text-tertiary">
        Threads land here when an agent stops and needs you, along with reviews
        assigned to you.
      </p>
    </div>
  )
}

function InboxSkeleton() {
  return (
    <div aria-hidden>
      {[80, 64, 72, 58].map((width) => (
        <div key={width} className="mb-0.5 flex flex-col gap-1.5 px-2.5 py-2.5">
          <Skeleton className="h-2.5" style={{ width: `${width}%` }} />
          <Skeleton className="h-2 w-1/3" />
        </div>
      ))}
    </div>
  )
}
