import { Button } from "@langchain/macaw-components/Button"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Kbd } from "@langchain/macaw-components/Kbd"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"
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
  formatSnoozeTime,
  SNOOZE_OPTIONS,
  useInboxSnoozes,
  useSetInboxSnooze,
} from "@/features/agents/lib/inboxSnoozes"
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
  const snoozes = useInboxSnoozes()
  const setSnooze = useSetInboxSnooze().mutate
  const now = useWakeClock(
    useMemo(
      () => (snoozes.data ?? []).map((snooze) => snooze.until_ms),
      [snoozes.data]
    )
  )
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
  const [snoozing, setSnoozing] = useState<InboxItem | null>(null)
  const list = useRef<HTMLDivElement>(null)

  const reviewRows = useMemo(
    () => reviews.data?.pages.flatMap((page) => page.pullRequests) ?? [],
    [reviews.data]
  )
  const inbox = buildInbox({
    threads: withoutNestedWorkers(threads.items),
    reviews: reviewRows,
    done: doneReviews,
    snoozes: snoozes.data ?? [],
    now,
  })
  const waitingCount = inbox.filter(
    (item) => item.snoozedUntil === undefined
  ).length
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
    const advance = () =>
      setSelection(selectionAfter(latest.current.items, latest.current.index))
    const done = () => {
      const item = latest.current.items[latest.current.index]
      if (!item) return
      advance()
      if (item.kind === "review")
        setDoneReviews((keys) => new Set([...keys, item.key]))
      else resolveThread({ threadId: item.thread.id, resolved: true })
    }
    const snooze = () => {
      const item = latest.current.items[latest.current.index]
      if (!item) return
      if (item.snoozedUntil === undefined) {
        setSnoozing(item)
        return
      }
      advance()
      setSnooze({ key: item.key, untilMs: null })
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
        id: "inbox-snooze",
        label: "Snooze, or move a snoozed item back to the inbox",
        aliases: ["snooze", "remind me", "unsnooze"],
        shortcuts: ["h"],
        group: "Inbox",
        run: snooze,
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
  }, [chat.thread, navigate, resolveThread, setSnooze])
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
        <header className="flex flex-col gap-space-3 px-space-4 pt-5 pb-space-3">
          <div className="flex items-center gap-space-2">
            <TrayIcon size={18} weight="regular" />
            <h1 className="font-heading text-base font-medium text-primary">
              Inbox
            </h1>
            <span className="text-xs text-tertiary">{waitingCount}</span>
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
          className="min-h-0 flex-1 overflow-y-auto px-space-2 pb-space-2 outline-none"
        >
          {loading && items.length === 0 ? (
            <InboxSkeleton />
          ) : items.length === 0 ? (
            <InboxZero split={split} />
          ) : (
            items.map((item, itemIndex) => (
              <InboxRow
                key={item.key}
                item={item}
                now={now}
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
              className="mt-space-1 w-full"
              disabled={threads.isFetchingNextPage}
              onClick={threads.fetchNextPage}
            >
              {threads.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          )}
        </div>
        <footer className="flex flex-wrap gap-x-space-3 gap-y-space-1 border-t border-default px-space-4 py-space-2 text-xxs text-tertiary">
          <span>
            <Kbd>J</Kbd>/<Kbd>K</Kbd> move
          </span>
          <span>
            <Kbd>E</Kbd> done
          </span>
          <span>
            <Kbd>H</Kbd> {split === "snoozed" ? "unsnooze" : "snooze"}
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
        {snoozing && (
          <SnoozePicker
            item={snoozing}
            onClose={() => setSnoozing(null)}
            onPick={(untilMs) => {
              setSnoozing(null)
              const { items: current, index: at } = latest.current
              if (current[at]?.key === snoozing.key)
                setSelection(selectionAfter(current, at))
              setSnooze({ key: snoozing.key, untilMs })
            }}
          />
        )}
      </section>
    </div>
  )
}

/** The selection once the item at `at` leaves the list: the next item, or the last. */
function selectionAfter(
  items: ReadonlyArray<InboxItem>,
  at: number
): { key?: string; index: number } {
  const neighbor = items[at + 1] ? at + 1 : at - 1
  return { key: items[neighbor]?.key, index: Math.max(at, 0) }
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
  now,
  selected,
  onSelect,
}: {
  item: InboxItem
  now: number
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
        "mb-0.5 flex cursor-default gap-2.5 rounded-lg px-2.5 py-space-2 transition-colors",
        selected ? "bg-selected" : "hover:bg-surface-level-2-hover"
      )}
    >
      <Icon
        size={16}
        weight="regular"
        className="mt-0.5 shrink-0 text-icon-secondary"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-space-2">
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
          {item.snoozedUntil !== undefined ? (
            <span className="flex shrink-0 items-center gap-space-1 text-secondary">
              <ClockIcon size={12} weight="regular" />
              Until{" "}
              {formatSnoozeTime(new Date(item.snoozedUntil), new Date(now))}
            </span>
          ) : (
            <span className={cn("shrink-0", TONE_TEXT[item.tone])}>
              {item.reason}
              {item.backFromSnooze === "activity"
                ? " · changed while snoozed"
                : item.backFromSnooze === "time"
                  ? " · back from snooze"
                  : ""}
            </span>
          )}
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
    <div className="m-auto flex max-w-md flex-col items-center gap-space-3 p-space-5 text-center">
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

function InboxZero({ split }: { split: InboxSplit }) {
  if (split === "snoozed")
    return (
      <div className="flex flex-col items-center gap-space-2 px-space-5 py-space-9 text-center">
        <ClockIcon size={28} weight="regular" className="text-icon-tertiary" />
        <p className="text-sm font-medium text-primary">Nothing snoozed</p>
        <p className="text-xs text-tertiary">
          Press <Kbd>H</Kbd> on an item to hide it until later. It comes back
          sooner if it changes.
        </p>
      </div>
    )
  return (
    <div className="flex flex-col items-center gap-space-2 px-space-5 py-space-9 text-center">
      <TrayIcon size={28} weight="regular" className="text-icon-tertiary" />
      <p className="text-sm font-medium text-primary">You're all caught up</p>
      <p className="text-xs text-tertiary">
        Threads land here when an agent stops and needs you, along with reviews
        assigned to you.
      </p>
    </div>
  )
}

// setTimeout overflows past ~24.8 days; a longer wait just re-arms when it fires.
const MAX_TIMEOUT_MS = 2 ** 31 - 1

/** The current time, refreshed the moment the next of `wakeTimes` passes. */
function useWakeClock(wakeTimes: ReadonlyArray<number>): number {
  const [now, setNow] = useState(() => Date.now())
  const next = Math.min(...wakeTimes.filter((time) => time > now))
  useEffect(() => {
    if (!Number.isFinite(next)) return
    const timer = window.setTimeout(
      () => setNow(Date.now()),
      Math.min(next - Date.now() + 50, MAX_TIMEOUT_MS)
    )
    return () => window.clearTimeout(timer)
  }, [next])
  return now
}

/**
 * Picks when a snoozed item comes back. While open it owns the keyboard, so the
 * number keys pick an option and the inbox shortcuts stay put.
 */
function SnoozePicker({
  item,
  onPick,
  onClose,
}: {
  item: InboxItem
  onPick: (untilMs: number) => void
  onClose: () => void
}) {
  const [opened] = useState(() => new Date())
  const latest = useRef({ onPick, onClose })
  useEffect(() => {
    latest.current = { onPick, onClose }
  })
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey) return
      event.preventDefault()
      event.stopImmediatePropagation()
      if (event.key === "Escape" || event.key.toLowerCase() === "h") {
        latest.current.onClose()
        return
      }
      const option = SNOOZE_OPTIONS.find(
        (candidate) => candidate.shortcut === event.key
      )
      if (option) latest.current.onPick(option.until(new Date()).getTime())
    }
    window.addEventListener("keydown", onKeyDown, true)
    return () => window.removeEventListener("keydown", onKeyDown, true)
  }, [])

  return (
    <div
      className="absolute inset-0 z-popover flex items-start justify-center bg-black/20 pt-24"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label="Snooze until"
        className="w-72 rounded-xl border border-default bg-surface-level-1 p-space-2 shadow-lg"
        onClick={(event) => event.stopPropagation()}
      >
        <p className="truncate px-space-2 pt-space-1 pb-space-2 text-xs text-tertiary">
          Snooze “{item.title}”
        </p>
        {SNOOZE_OPTIONS.map((option) => (
          <button
            key={option.shortcut}
            type="button"
            className="flex w-full items-center gap-space-2 rounded-lg px-space-2 py-1.5 text-left text-sm text-primary hover:bg-surface-level-2-hover"
            onClick={() => onPick(option.until(new Date()).getTime())}
          >
            <Kbd>{option.shortcut}</Kbd>
            <span className="flex-1">{option.label}</span>
            <span className="text-xs text-tertiary">
              {formatSnoozeTime(option.until(opened), opened)}
            </span>
          </button>
        ))}
        <p className="px-space-2 pt-space-2 text-xxs text-tertiary">
          It comes back sooner if it changes. <Kbd>Esc</Kbd> to cancel.
        </p>
      </div>
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
