import { Dialog } from "@base-ui/react/dialog"
import { useNavigate } from "@tanstack/react-router"
import {
  Command as CommandIcon,
  GitPullRequest,
  Laptop,
  LoaderCircle,
  MessageSquare,
  Search,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import type { AppCommand } from "@/lib/appCommands"
import type { PullRequestSearchResult } from "@/lib/api"
import { usePullRequestSearch } from "@/features/reviews/lib/usePullRequestSearch"
import type { AgentThread } from "@/features/agents/lib/types"
import type { DesktopLegacyLocalThread } from "@/desktop"
import { Kbd } from "@langchain/gtm-platform-design-system/ui/kbd"
import { useInfiniteThreadsPages } from "@/features/agents/lib/queries"
import { useLegacyLocalThreads } from "@/features/agents/lib/legacyLocal"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import { useShortcutLabel } from "@/lib/hotkeys"
import { cn } from "@/lib/utils"
import { useChatRoutes } from "@/lib/chatRoutes"

interface CommandResult {
  id: string
  kind: "command"
  label: string
  command: AppCommand
}

interface CloudThreadResult {
  id: string
  kind: "cloud-thread"
  label: string
  thread: AgentThread
}

interface LocalThreadResult {
  id: string
  kind: "local-thread"
  label: string
  thread: DesktopLegacyLocalThread
}

interface PullRequestResult {
  id: string
  kind: "pull-request"
  label: string
  pr: PullRequestSearchResult
}

type PaletteResult =
  | CommandResult
  | CloudThreadResult
  | LocalThreadResult
  | PullRequestResult

function ShortcutHint({ shortcut }: { shortcut: string }) {
  const label = useShortcutLabel(shortcut)
  return <Kbd className="ml-auto bg-canvas/70">{label}</Kbd>
}

function commandMatches(command: AppCommand, query: string): boolean {
  const haystack = [command.label, ...(command.aliases ?? [])]
    .join(" ")
    .toLowerCase()
  return haystack.includes(query.toLowerCase())
}

export function buildPaletteResults(
  commands: ReadonlyArray<AppCommand>,
  cloudThreads: ReadonlyArray<AgentThread>,
  localThreads: ReadonlyArray<DesktopLegacyLocalThread>,
  query: string
): Array<PaletteResult> {
  const normalizedQuery = query.trim().toLowerCase()
  const actionsOnly = normalizedQuery.startsWith(">")
  const searchQuery = actionsOnly
    ? normalizedQuery.slice(1).trim()
    : normalizedQuery
  const commandResults: Array<CommandResult> = commands
    .filter(
      (command) =>
        command.run &&
        command.showInPalette !== false &&
        (!searchQuery || commandMatches(command, searchQuery))
    )
    .map((command) => ({
      id: `command:${command.id}`,
      kind: "command",
      label: command.label,
      command,
    }))
  const cloudResults: Array<CloudThreadResult> = cloudThreads
    .filter(
      (thread) =>
        !actionsOnly &&
        (!searchQuery ||
          [
            thread.title,
            thread.repo,
            thread.repoFullName,
            thread.branch,
            thread.pr?.url ?? "",
            `${thread.repoFullName}#${thread.pr?.number ?? ""}`,
          ]
            .join(" ")
            .toLowerCase()
            .includes(searchQuery))
    )
    .map((thread) => ({
      id: `cloud:${thread.id}`,
      kind: "cloud-thread",
      label: thread.title,
      thread,
    }))
  const localResults: Array<LocalThreadResult> = localThreads
    .filter(
      (thread) =>
        !actionsOnly &&
        (!searchQuery ||
          [thread.title, thread.cwd]
            .join(" ")
            .toLowerCase()
            .includes(searchQuery))
    )
    .map((thread) => ({
      id: `local:${thread.id}`,
      kind: "local-thread",
      label: thread.title,
      thread,
    }))
  return [...commandResults, ...cloudResults, ...localResults]
}

export function AppCommandPalette({
  commands,
  open,
  onOpenChange,
}: {
  commands: ReadonlyArray<AppCommand>
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const navigate = useNavigate()
  const chat = useChatRoutes()
  const [query, setQuery] = useState("")
  const [debouncedQuery, setDebouncedQuery] = useState("")
  const [activeHighlight, setActiveHighlight] = useState({ key: "", index: 0 })
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)

  useEffect(() => {
    if (!open) {
      // oxlint-disable-next-line react/set-state-in-effect
      setQuery("")
      setDebouncedQuery("")
      return
    }
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 180)
    return () => window.clearTimeout(timer)
  }, [open, query])

  const cloudThreads = useInfiniteThreadsPages(
    {
      limit: 20,
      q: debouncedQuery || undefined,
      scope: "interactive",
    },
    { enabled: open, staleWhileRevalidate: true }
  )
  const localThreads = useLegacyLocalThreads({ enabled: open && isDesktop })
  const pullRequests = usePullRequestSearch(
    query,
    open && !query.trim().startsWith(">")
  )
  const results = useMemo(
    () => [
      ...buildPaletteResults(
        commands,
        cloudThreads.data?.pages.flatMap((page) => page.items) ?? [],
        localThreads.data ?? [],
        query
      ),
      ...(open && !query.trim().startsWith(">")
        ? (pullRequests.data?.pages.flatMap((page) =>
            page.pull_requests.map((pr): PullRequestResult => ({
              id: `pr:${pr.repo}#${pr.number}`,
              kind: "pull-request",
              label: pr.title || `${pr.repo} #${pr.number}`,
              pr,
            }))
          ) ?? [])
        : []),
    ],
    [
      cloudThreads.data?.pages,
      commands,
      localThreads.data,
      query,
      open,
      pullRequests.data,
    ]
  )
  const resultGroups = useMemo(() => {
    const grouped = new Map<string, Array<PaletteResult>>()
    for (const result of results) {
      const group =
        result.kind === "command"
          ? result.command.group
          : result.kind === "cloud-thread"
            ? "Threads"
            : result.kind === "pull-request"
              ? "Pull requests"
              : "This Mac"
      grouped.set(group, [...(grouped.get(group) ?? []), result])
    }
    return [...grouped]
  }, [results])
  const resultKey = results.map((result) => result.id).join("|")

  const activeIndex =
    activeHighlight.key === resultKey ? activeHighlight.index : 0
  const setActiveIndex = (next: number | ((current: number) => number)) => {
    setActiveHighlight({
      key: resultKey,
      index: typeof next === "function" ? next(activeIndex) : next,
    })
  }

  const runResult = (result: PaletteResult | undefined) => {
    if (!result) return
    onOpenChange(false)
    if (result.kind === "command") {
      void result.command.run?.()
    } else if (result.kind === "cloud-thread" && result.thread.reviewPage) {
      void navigate(reviewPageRoute(result.thread.reviewPage))
    } else if (result.kind === "cloud-thread") {
      void navigate({
        to: chat.thread,
        params: { threadId: result.thread.id },
      })
    } else if (result.kind === "pull-request") {
      const [owner, repo] = result.pr.repo.split("/")
      if (owner && repo) {
        void navigate(
          reviewPageRoute({ owner, repo, number: result.pr.number })
        )
      }
    } else {
      void navigate({
        to: "/agents/local/$sessionId",
        params: { sessionId: result.thread.id },
      })
    }
  }

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault()
      setActiveIndex((current) =>
        results.length === 0 ? 0 : (current + 1) % results.length
      )
    } else if (event.key === "ArrowUp") {
      event.preventDefault()
      setActiveIndex((current) =>
        results.length === 0
          ? 0
          : (current - 1 + results.length) % results.length
      )
    } else if (event.key === "Home") {
      event.preventDefault()
      setActiveIndex(0)
    } else if (event.key === "End") {
      event.preventDefault()
      setActiveIndex(Math.max(0, results.length - 1))
    } else if (event.key === "Enter") {
      event.preventDefault()
      runResult(results[activeIndex])
    }
  }

  const showLoading =
    (cloudThreads.isFetching || pullRequests.isSearching) &&
    results.length === 0
  const showError = cloudThreads.isError && results.length === 0

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/45 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup
          className="fixed top-[18%] left-1/2 z-50 flex max-h-[min(34rem,70vh)] w-[min(40rem,calc(100vw-2rem))] -translate-x-1/2 flex-col overflow-hidden rounded-control border border-line bg-panel text-ink shadow-overlay outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95"
          data-hotkeys="ignore"
        >
          <Dialog.Title className="sr-only">
            Search commands, threads, and pull requests
          </Dialog.Title>
          <Dialog.Description className="sr-only">
            Search commands, threads, and pull request titles and descriptions.
          </Dialog.Description>
          <div className="flex items-center gap-2 border-b border-line px-4">
            <Search className="size-4 shrink-0 text-ink-subtle" />
            <input
              autoFocus
              aria-activedescendant={results[activeIndex]?.id}
              aria-autocomplete="list"
              aria-controls="app-command-results"
              className="h-12 min-w-0 flex-1 bg-transparent text-body outline-none placeholder:text-ink-subtle"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={onInputKeyDown}
              placeholder="Search commands, threads, and pull requests…"
              role="combobox"
              value={query}
            />
            <Kbd>Esc</Kbd>
          </div>
          <div
            className="min-h-20 overflow-y-auto p-2"
            id="app-command-results"
            role="listbox"
          >
            {showLoading ? (
              <div className="flex items-center justify-center gap-2 py-10 text-meta text-ink-subtle">
                <LoaderCircle className="size-4 animate-spin" />
                Searching threads and pull requests…
              </div>
            ) : showError ? (
              <p className="py-10 text-center text-label text-risk">
                Thread search is unavailable.
              </p>
            ) : results.length === 0 ? (
              <p className="py-10 text-center text-meta text-ink-subtle">
                No commands, threads, or pull requests found.
              </p>
            ) : (
              <>
                {resultGroups.map(([group, groupResults]) => (
                  <div aria-label={group} key={group} role="group">
                    <div className="px-3 pt-2 pb-1 text-meta font-semibold tracking-wide text-ink-subtle uppercase">
                      {group}
                    </div>
                    {groupResults.map((result) => {
                      const index = results.indexOf(result)
                      const command =
                        result.kind === "command" ? result.command : null
                      const Icon =
                        result.kind === "command"
                          ? CommandIcon
                          : result.kind === "local-thread"
                            ? Laptop
                            : result.kind === "pull-request"
                              ? GitPullRequest
                              : MessageSquare
                      return (
                        <button
                          aria-selected={index === activeIndex}
                          className={cn(
                            "flex w-full items-center gap-3 rounded-compact px-3 py-2 text-left text-body",
                            index === activeIndex
                              ? "bg-hover text-ink"
                              : "text-ink"
                          )}
                          id={result.id}
                          key={result.id}
                          onClick={() => runResult(result)}
                          onMouseEnter={() => setActiveIndex(index)}
                          role="option"
                          type="button"
                        >
                          <Icon className="size-4 shrink-0 text-ink-subtle" />
                          <span className="min-w-0 flex-1 truncate">
                            {result.label}
                            {result.kind === "pull-request" && (
                              <span className="ml-2 text-meta text-ink-subtle">
                                {result.pr.repo} #{result.pr.number} ·{" "}
                                {result.pr.state}
                              </span>
                            )}
                          </span>
                          {command?.shortcuts?.[0] && (
                            <ShortcutHint shortcut={command.shortcuts[0]} />
                          )}
                        </button>
                      )
                    })}
                  </div>
                ))}
                {pullRequests.hasNextPage && !query.trim().startsWith(">") && (
                  <button
                    className="flex w-full items-center justify-center gap-2 rounded-compact px-3 py-2 text-meta text-ink-subtle hover:bg-hover hover:text-ink"
                    disabled={pullRequests.isFetchingNextPage}
                    onClick={() => void pullRequests.fetchNextPage()}
                    type="button"
                  >
                    Load more pull requests
                  </button>
                )}
                {cloudThreads.hasNextPage && (
                  <button
                    className="flex w-full items-center justify-center gap-2 rounded-compact px-3 py-2 text-meta text-ink-subtle hover:bg-hover hover:text-ink"
                    disabled={cloudThreads.isFetchingNextPage}
                    onClick={() => void cloudThreads.fetchNextPage()}
                    type="button"
                  >
                    {cloudThreads.isFetchingNextPage && (
                      <LoaderCircle className="size-3.5 animate-spin" />
                    )}
                    Load more threads
                  </button>
                )}
              </>
            )}
            {query.trim() &&
              !query.trim().startsWith(">") &&
              pullRequests.isError && (
                <p role="alert" className="px-3 py-2 text-label text-risk">
                  Pull request search is unavailable.
                </p>
              )}
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
