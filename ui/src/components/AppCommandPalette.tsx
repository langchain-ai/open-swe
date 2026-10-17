import { MagnifyingGlassRegularIcon } from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@langchain/macaw-components/Command"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import { Kbd } from "@langchain/macaw-components/Kbd"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { CommandIcon } from "@phosphor-icons/react/dist/ssr/Command"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { useNavigate } from "@tanstack/react-router"
import { useEffect, useMemo, useState } from "react"

import type { AppCommand } from "@/lib/appCommands"
import type { PullRequestSearchResult } from "@/lib/api"
import { usePullRequestSearch } from "@/features/reviews/lib/usePullRequestSearch"
import type { AgentThread } from "@/features/agents/lib/types"
import { useInfiniteThreadsPages } from "@/features/agents/lib/queries"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import { useShortcutLabel } from "@/lib/hotkeys"
import { useChatRoutes } from "@/lib/chatRoutes"
import { useAiSearchStream } from "@/lib/useAiSearchStream"

interface CommandResult {
  id: string
  kind: "command"
  label: string
  command: AppCommand
}

interface CloudThreadResult {
  id: string
  kind: "cloud-thread" | "related-thread"
  label: string
  thread: AgentThread
}

interface PullRequestResult {
  id: string
  kind: "pull-request" | "related-pull-request"
  label: string
  pr: PullRequestSearchResult
}

type PaletteResult = CommandResult | CloudThreadResult | PullRequestResult

const AI_SEARCH_DEBOUNCE_MS = 500
const AI_SEARCH_MIN_LENGTH = 3
const AI_SEARCH_SHOWN = 8

type RelatedThread = AgentThread & { relevance: number }
type RelatedPullRequest = PullRequestSearchResult & { relevance: number }

const threadKey = (thread: RelatedThread) => thread.id
const pullRequestKey = (pr: PullRequestSearchResult) =>
  `${pr.repo}#${pr.number}`

const RESULT_ICONS = {
  command: CommandIcon,
  "cloud-thread": ChatCircleIcon,
  "related-thread": SparkleIcon,
  "pull-request": GitPullRequestIcon,
  "related-pull-request": SparkleIcon,
} as const

function resultGroup(result: PaletteResult): string {
  switch (result.kind) {
    case "command":
      return result.command.group
    case "cloud-thread":
      return "Threads"
    case "related-thread":
      return "Related threads"
    case "pull-request":
      return "Pull requests"
    case "related-pull-request":
      return "Related pull requests"
  }
}

function ShortcutHint({ shortcut }: { shortcut: string }) {
  const label = useShortcutLabel(shortcut)
  return <Kbd className="ml-auto">{label}</Kbd>
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
  return [...commandResults, ...cloudResults]
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
  const [aiQuery, setAiQuery] = useState("")
  const trimmedQuery = query.trim()
  const actionsOnly = trimmedQuery.startsWith(">")

  useEffect(() => {
    if (!open) {
      // oxlint-disable-next-line react/set-state-in-effect
      setQuery("")
      setDebouncedQuery("")
      setAiQuery("")
      return
    }
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 180)
    const aiTimer = window.setTimeout(
      () => setAiQuery(query.trim()),
      AI_SEARCH_DEBOUNCE_MS
    )
    return () => {
      window.clearTimeout(timer)
      window.clearTimeout(aiTimer)
    }
  }, [open, query])

  const cloudThreads = useInfiniteThreadsPages(
    {
      limit: 20,
      q: debouncedQuery || undefined,
      scope: "interactive",
    },
    { enabled: open, staleWhileRevalidate: true }
  )
  const pullRequests = usePullRequestSearch(query, open && !actionsOnly)
  const aiSearchEnabled =
    open &&
    !actionsOnly &&
    aiQuery === trimmedQuery &&
    aiQuery.length >= AI_SEARCH_MIN_LENGTH
  const relatedThreads = useAiSearchStream<RelatedThread>(
    aiSearchEnabled
      ? `/threads/search?${new URLSearchParams({ q: aiQuery, scope: "interactive" })}`
      : null,
    threadKey
  )
  const relatedPullRequests = useAiSearchStream<RelatedPullRequest>(
    aiSearchEnabled
      ? `/pull-requests/related?${new URLSearchParams({ q: aiQuery })}`
      : null,
    pullRequestKey
  )
  const results = useMemo(() => {
    const keywordResults = buildPaletteResults(
      commands,
      cloudThreads.data?.pages.flatMap((page) => page.items) ?? [],
      query
    )
    const shownThreadIds = new Set(
      keywordResults.flatMap((result) =>
        result.kind === "cloud-thread" ? [result.thread.id] : []
      )
    )
    const relatedThreadResults = relatedThreads.matches
      .filter((thread) => !shownThreadIds.has(thread.id))
      .slice(0, AI_SEARCH_SHOWN)
      .map((thread): CloudThreadResult => ({
        id: `related:${thread.id}`,
        kind: "related-thread",
        label: thread.title,
        thread,
      }))
    const keywordPullRequests =
      open && !actionsOnly
        ? (pullRequests.data?.pages.flatMap((page) => page.pull_requests) ?? [])
        : []
    const shownPullRequests = new Set(keywordPullRequests.map(pullRequestKey))
    const pullRequestResult =
      (kind: PullRequestResult["kind"]) =>
      (pr: PullRequestSearchResult): PullRequestResult => ({
        id: `${kind}:${pullRequestKey(pr)}`,
        kind,
        label: pr.title || `${pr.repo} #${pr.number}`,
        pr,
      })
    return [
      ...keywordResults,
      ...relatedThreadResults,
      ...keywordPullRequests.map(pullRequestResult("pull-request")),
      ...relatedPullRequests.matches
        .filter((pr) => !shownPullRequests.has(pullRequestKey(pr)))
        .slice(0, AI_SEARCH_SHOWN)
        .map(pullRequestResult("related-pull-request")),
    ]
  }, [
    actionsOnly,
    cloudThreads.data?.pages,
    commands,
    query,
    open,
    pullRequests.data,
    relatedPullRequests.matches,
    relatedThreads.matches,
  ])
  const resultGroups = useMemo(() => {
    const grouped = new Map<string, Array<PaletteResult>>()
    for (const result of results) {
      const group = resultGroup(result)
      grouped.set(group, [...(grouped.get(group) ?? []), result])
    }
    return [...grouped]
  }, [results])
  const runResult = (result: PaletteResult | undefined) => {
    if (!result) return
    onOpenChange(false)
    if (result.kind === "command") {
      void result.command.run?.()
    } else if ("thread" in result && result.thread.reviewPage) {
      void navigate(reviewPageRoute(result.thread.reviewPage))
    } else if ("thread" in result) {
      void navigate({
        to: chat.thread,
        params: { threadId: result.thread.id },
      })
    } else {
      const [owner, repo] = result.pr.repo.split("/")
      if (owner && repo) {
        void navigate(
          reviewPageRoute({ owner, repo, number: result.pr.number })
        )
      }
    }
  }

  const searchingRelated =
    relatedThreads.status === "streaming" ||
    relatedPullRequests.status === "streaming"
  const relatedScanned = relatedThreads.scanned + relatedPullRequests.scanned
  const showLoading =
    (cloudThreads.isFetching || pullRequests.isSearching || searchingRelated) &&
    results.length === 0
  const showError = cloudThreads.isError && results.length === 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="top-[18%] max-h-[min(34rem,70vh)] w-[min(40rem,calc(100vw-2rem))] translate-y-0 overflow-hidden rounded-xl border border-default bg-elevated text-primary shadow-lg"
        childrenClassName="gap-0 p-0"
        showClose={false}
      >
        <DialogTitle className="sr-only">
          Search commands, threads, and pull requests
        </DialogTitle>
        <DialogDescription className="sr-only">
          Search commands, threads, and pull request titles and descriptions.
        </DialogDescription>
        <Command
          className="min-h-0 flex-1 bg-transparent"
          data-hotkeys="ignore"
          label="Search commands, threads, and pull requests"
          loop
          shouldFilter={false}
        >
          <CommandInput
            autoFocus
            className="h-12 shrink-0 rounded-none border-0 border-b border-default bg-transparent px-space-4 focus-within:border-default"
            leftDecorator={
              <MagnifyingGlassRegularIcon
                className="text-icon-secondary"
                size={16}
              />
            }
            onValueChange={setQuery}
            placeholder="Search commands, threads, and pull requests…"
            rightDecorator={<Kbd>Esc</Kbd>}
            size="md"
            value={query}
            variant="plain"
          />
          <CommandList className="max-h-none min-h-20 flex-1 p-space-2">
            {showLoading ? (
              <div className="flex items-center justify-center gap-space-2 py-space-7 text-xs text-secondary">
                <Spinner size="xs" />
                Searching threads and pull requests…
              </div>
            ) : showError ? (
              <p className="py-space-7 text-center text-xs text-error-secondary">
                Thread search is unavailable.
              </p>
            ) : results.length === 0 ? (
              <p className="py-space-7 text-center text-xs text-secondary">
                No commands, threads, or pull requests found.
              </p>
            ) : (
              <>
                {resultGroups.map(([group, groupResults]) => (
                  <CommandGroup heading={group} key={group}>
                    {groupResults.map((result) => {
                      const command =
                        result.kind === "command" ? result.command : null
                      const Icon = RESULT_ICONS[result.kind]
                      return (
                        <CommandItem
                          className="gap-space-3 rounded-md px-space-3 py-space-2 text-primary"
                          key={result.id}
                          onSelect={() => runResult(result)}
                          value={result.id}
                        >
                          <Icon
                            className="shrink-0 text-icon-secondary"
                            size={16}
                            weight="regular"
                          />
                          <span className="min-w-0 flex-1 truncate">
                            {result.label}
                            {"pr" in result && (
                              <span className="ml-space-2 text-xs text-secondary">
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
                {searchingRelated && (
                  <div className="flex items-center gap-space-2 px-space-3 py-space-2 text-xs text-secondary">
                    <Spinner size="xs" />
                    {relatedScanned
                      ? `Searching with AI… ${relatedScanned} checked`
                      : "Searching with AI…"}
                  </div>
                )}
                {pullRequests.hasNextPage && !actionsOnly && (
                  <Button
                    className="w-full"
                    color="secondary"
                    disabled={pullRequests.isFetchingNextPage}
                    loading={pullRequests.isFetchingNextPage}
                    onClick={() => void pullRequests.fetchNextPage()}
                    size="xs"
                    variant="plain"
                  >
                    Load more pull requests
                  </Button>
                )}
                {cloudThreads.hasNextPage && (
                  <Button
                    className="w-full"
                    color="secondary"
                    disabled={cloudThreads.isFetchingNextPage}
                    loading={cloudThreads.isFetchingNextPage}
                    onClick={() => void cloudThreads.fetchNextPage()}
                    size="xs"
                    variant="plain"
                  >
                    Load more threads
                  </Button>
                )}
              </>
            )}
            {trimmedQuery && !actionsOnly && pullRequests.isError && (
              <p
                role="alert"
                className="px-space-3 py-space-2 text-xs text-error-secondary"
              >
                Pull request search is unavailable.
              </p>
            )}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  )
}
