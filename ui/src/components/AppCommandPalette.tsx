import { useNavigate } from "@tanstack/react-router"
import { useEffect, useMemo, useState } from "react"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Command,
  CommandEmpty,
  CommandFooter,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@langchain/gtm-platform-design-system/ui/command"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/gtm-platform-design-system/ui/dialog"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"

import type { Glyph } from "@/components/glyphs"
import type { AppCommand } from "@/lib/appCommands"
import type { PullRequestSearchResult } from "@/lib/api"
import type { AgentThread } from "@/features/agents/lib/types"
import type { DesktopLegacyLocalThread } from "@/desktop"
import {
  GitPullRequest,
  MessageSquare,
  Monitor,
  Terminal,
} from "@/components/glyphs"
import { ShortcutChord } from "@/components/AppShortcutReference"
import { usePullRequestSearch } from "@/features/reviews/lib/usePullRequestSearch"
import { useInfiniteThreadsPages } from "@/features/agents/lib/queries"
import { useLegacyLocalThreads } from "@/features/agents/lib/legacyLocal"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
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

const RESULT_GLYPH: Record<PaletteResult["kind"], Glyph> = {
  command: Terminal,
  "cloud-thread": MessageSquare,
  "local-thread": Monitor,
  "pull-request": GitPullRequest,
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

  const showLoading =
    (cloudThreads.isFetching || pullRequests.isSearching) &&
    results.length === 0
  const showError = cloudThreads.isError && results.length === 0

  const showPullRequestMore =
    pullRequests.hasNextPage && !query.trim().startsWith(">")
  const showPullRequestError =
    query.trim() && !query.trim().startsWith(">") && pullRequests.isError

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        data-hotkeys="ignore"
        className="top-24 w-full max-w-xl translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-xl"
      >
        <DialogTitle className="sr-only">
          Search commands, threads, and pull requests
        </DialogTitle>
        <DialogDescription className="sr-only">
          Search commands, threads, and pull request titles and descriptions.
        </DialogDescription>
        <Command
          size="palette"
          shouldFilter={false}
          loop
          className="rounded-none bg-transparent"
        >
          <CommandInput
            autoFocus
            value={query}
            onValueChange={setQuery}
            placeholder="Search commands, threads, and pull requests…"
            shortcut={["esc"]}
          />
          <CommandList>
            <CommandEmpty>
              {showLoading ? (
                <Inline
                  gap="sm"
                  align="center"
                  justify="center"
                  ink="ink-subtle"
                >
                  <Spinner size="sm" />
                  Searching threads and pull requests…
                </Inline>
              ) : showError ? (
                <Box render={<span />} className="text-risk">
                  Thread search is unavailable.
                </Box>
              ) : (
                "No commands, threads, or pull requests found."
              )}
            </CommandEmpty>
            {resultGroups.map(([group, groupResults]) => (
              <CommandGroup key={group} heading={group}>
                {groupResults.map((result) => {
                  const command =
                    result.kind === "command" ? result.command : null
                  return (
                    <CommandItem
                      key={result.id}
                      value={result.id}
                      onSelect={() => runResult(result)}
                    >
                      <Icon
                        icon={RESULT_GLYPH[result.kind]}
                        className="text-ink-subtle"
                      />
                      <Box
                        render={<span />}
                        className="min-w-0 flex-1 truncate"
                      >
                        {result.label}
                        {result.kind === "pull-request" && (
                          <Box
                            render={<span />}
                            className="ml-2 text-meta text-ink-subtle"
                          >
                            {result.pr.repo} #{result.pr.number} ·{" "}
                            {result.pr.state}
                          </Box>
                        )}
                      </Box>
                      {command?.shortcuts?.[0] && (
                        <ShortcutChord shortcut={command.shortcuts[0]} />
                      )}
                    </CommandItem>
                  )
                })}
              </CommandGroup>
            ))}
            {showPullRequestError && (
              <Box
                render={<p />}
                role="alert"
                className="px-4 py-2 text-label text-risk"
              >
                Pull request search is unavailable.
              </Box>
            )}
          </CommandList>
        </Command>
        {(showPullRequestMore || cloudThreads.hasNextPage) && (
          <CommandFooter className="justify-center">
            {showPullRequestMore && (
              <Button
                variant="ghost"
                size="compact"
                loading={pullRequests.isFetchingNextPage}
                onClick={() => void pullRequests.fetchNextPage()}
              >
                Load more pull requests
              </Button>
            )}
            {cloudThreads.hasNextPage && (
              <Button
                variant="ghost"
                size="compact"
                aria-label="Load more threads"
                loading={cloudThreads.isFetchingNextPage}
                onClick={() => void cloudThreads.fetchNextPage()}
              >
                Load more threads
              </Button>
            )}
          </CommandFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}
