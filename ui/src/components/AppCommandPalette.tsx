import { useNavigate } from "@tanstack/react-router"
import {
  Command as CommandIcon,
  GitPullRequest,
  Laptop,
  MessageSquare,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import type { AppCommand } from "@/lib/appCommands"
import type { PullRequestSearchResult } from "@/lib/api"
import { usePullRequestSearch } from "@/features/reviews/lib/usePullRequestSearch"
import type { AgentThread } from "@/features/agents/lib/types"
import type { DesktopLocalThreadSummary } from "@/desktop"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command"
import { Kbd } from "@/components/ui/kbd"
import { Spinner } from "@/components/ui/spinner"
import { useInfiniteThreadsPages } from "@/features/agents/lib/queries"
import { useDesktopLocalThreads } from "@/features/agents/lib/desktopLocal"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import { useShortcutLabel } from "@/lib/hotkeys"
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
  thread: DesktopLocalThreadSummary
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
  return (
    <CommandShortcut>
      <Kbd className="bg-background/70">{label}</Kbd>
    </CommandShortcut>
  )
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
  localThreads: ReadonlyArray<DesktopLocalThreadSummary>,
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
  const localThreads = useDesktopLocalThreads({ enabled: open && isDesktop })
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
            ? "Cloud threads"
            : result.kind === "pull-request"
              ? "Pull requests"
              : "This Mac"
      grouped.set(group, [...(grouped.get(group) ?? []), result])
    }
    return [...grouped]
  }, [results])

  const runResult = (result: PaletteResult) => {
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

  const showLoading =
    (cloudThreads.isFetching || pullRequests.isSearching) &&
    results.length === 0
  const showError = cloudThreads.isError && results.length === 0

  return (
    <CommandDialog
      className="max-w-[40rem]"
      description="Search commands, threads, and pull request titles and descriptions."
      onOpenChange={onOpenChange}
      open={open}
      title="Search commands, threads, and pull requests"
    >
      <Command data-hotkeys="ignore" loop shouldFilter={false}>
        <CommandInput
          autoFocus
          onValueChange={setQuery}
          placeholder="Search commands, threads, and pull requests…"
          value={query}
        />
        <CommandList className="max-h-[min(30rem,60vh)] min-h-20">
          {showLoading ? (
            <div className="flex items-center justify-center gap-2 py-10 text-xs text-muted-foreground">
              <Spinner className="size-4" />
              Searching threads and pull requests…
            </div>
          ) : showError ? (
            <p className="py-10 text-center text-xs text-destructive">
              Thread search is unavailable.
            </p>
          ) : (
            <CommandEmpty className="py-10 text-muted-foreground">
              No commands, threads, or pull requests found.
            </CommandEmpty>
          )}
          {resultGroups.map(([group, groupResults]) => (
            <CommandGroup heading={group} key={group}>
              {groupResults.map((result) => {
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
                  <CommandItem
                    key={result.id}
                    onSelect={() => runResult(result)}
                    value={result.id}
                  >
                    <Icon className="size-4 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate">
                      {result.label}
                      {result.kind === "pull-request" && (
                        <span className="ml-2 text-xs text-muted-foreground">
                          {result.pr.repo} #{result.pr.number} ·{" "}
                          {result.pr.state}
                        </span>
                      )}
                    </span>
                    {command?.shortcuts?.[0] && (
                      <ShortcutHint shortcut={command.shortcuts[0]} />
                    )}
                  </CommandItem>
                )
              })}
            </CommandGroup>
          ))}
          {pullRequests.hasNextPage && !query.trim().startsWith(">") && (
            <Button
              className="w-full text-muted-foreground"
              disabled={pullRequests.isFetchingNextPage}
              onClick={() => void pullRequests.fetchNextPage()}
              variant="ghost"
            >
              {pullRequests.isFetchingNextPage && <Spinner />}
              Load more pull requests
            </Button>
          )}
          {query.trim() &&
            !query.trim().startsWith(">") &&
            pullRequests.isError && (
              <p role="alert" className="px-3 py-2 text-xs text-destructive">
                Pull request search is unavailable.
              </p>
            )}
          {results.length > 0 && cloudThreads.hasNextPage && (
            <Button
              className="w-full text-muted-foreground"
              disabled={cloudThreads.isFetchingNextPage}
              onClick={() => void cloudThreads.fetchNextPage()}
              variant="ghost"
            >
              {cloudThreads.isFetchingNextPage && <Spinner />}
              Load more threads
            </Button>
          )}
        </CommandList>
      </Command>
    </CommandDialog>
  )
}
