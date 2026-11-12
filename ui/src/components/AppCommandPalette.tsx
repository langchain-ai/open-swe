import { FuzzySearch } from "@/lib/fuzzySearch"
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
import { useNavigate } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"

import type { AppCommand } from "@/lib/appCommands"
import type { PullRequestSearchResult } from "@/lib/api"
import { usePullRequestSearch } from "@/features/reviews/lib/usePullRequestSearch"
import type { AgentThread } from "@/features/agents/lib/types"
import { useInfiniteThreadsPages } from "@/features/agents/lib/queries"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import { parsePullRequestReference } from "@/features/reviews/search"
import { useShortcutLabel } from "@/lib/hotkeys"
import { cn, formatRelativeTime } from "@/lib/utils"

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

interface PullRequestResult {
  id: string
  kind: "pull-request"
  label: string
  pr: Pick<PullRequestSearchResult, "repo" | "number"> &
    Partial<Pick<PullRequestSearchResult, "state">>
}

type PaletteResult = CommandResult | CloudThreadResult | PullRequestResult

type PaletteScope = "all" | "cloud-thread" | "pull-request" | "command"

const SCOPES: ReadonlyArray<{ value: PaletteScope; display: string }> = [
  { value: "all", display: "All" },
  { value: "cloud-thread", display: "Threads" },
  { value: "pull-request", display: "Pull requests" },
  { value: "command", display: "Commands" },
]

function ShortcutHint({ shortcut }: { shortcut: string }) {
  const label = useShortcutLabel(shortcut)
  return <Kbd className="ml-auto">{label}</Kbd>
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
  const commandResults: Array<CommandResult> = new FuzzySearch(searchQuery)
    .rank(
      commands,
      (command) => command.label,
      (command) => command.aliases?.join(" ") ?? ""
    )
    .filter((command) => command.run && command.showInPalette !== false)
    .map((command) => ({
      id: `command:${command.id}`,
      kind: "command",
      label: command.label,
      command,
    }))
  const cloudResults: Array<CloudThreadResult> = cloudThreads
    .filter(() => !actionsOnly)
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
  const [query, setQuery] = useState("")
  const [debouncedQuery, setDebouncedQuery] = useState("")
  const [scope, setScope] = useState<PaletteScope>("all")
  const inputRef = useRef<HTMLInputElement>(null)

  const actionsOnly = scope === "command" || query.trim().startsWith(">")
  const showThreads = !actionsOnly && scope !== "pull-request"
  const showPullRequests = !actionsOnly && scope !== "cloud-thread"
  const prReference = actionsOnly ? null : parsePullRequestReference(query)
  const threadQuery = prReference
    ? `https://github.com/${prReference.owner}/${prReference.repo}/pull/${prReference.number}`
    : query.trim()

  useEffect(() => {
    if (!open) {
      // oxlint-disable-next-line react/set-state-in-effect
      setQuery("")
      setDebouncedQuery("")
      setScope("all")
      return
    }
    const timer = window.setTimeout(() => setDebouncedQuery(threadQuery), 180)
    return () => window.clearTimeout(timer)
  }, [open, threadQuery])
  const cloudThreads = useInfiniteThreadsPages(
    {
      limit: 20,
      q: debouncedQuery || undefined,
      scope: prReference ? "all" : "interactive",
    },
    { enabled: open && showThreads, staleWhileRevalidate: true }
  )
  const pullRequests = usePullRequestSearch(
    query,
    open && showPullRequests && !prReference
  )
  const results: Array<PaletteResult> = [
    ...buildPaletteResults(
      actionsOnly || scope === "all" ? commands : [],
      showThreads && debouncedQuery === threadQuery
        ? (cloudThreads.data?.pages.flatMap((page) => page.items) ?? [])
        : [],
      query
    ),
    ...(prReference && showPullRequests
      ? [
          {
            id: `pr:${prReference.owner}/${prReference.repo}#${prReference.number}`,
            kind: "pull-request" as const,
            label: "Open Review Page",
            pr: {
              repo: `${prReference.owner}/${prReference.repo}`,
              number: prReference.number,
            },
          },
        ]
      : []),
    ...(open && showPullRequests && !prReference
      ? (pullRequests.data?.pages.flatMap((page) =>
          page.pull_requests.map((pr): PullRequestResult => ({
            id: `pr:${pr.repo}#${pr.number}`,
            kind: "pull-request",
            label: pr.title || `${pr.repo} #${pr.number}`,
            pr,
          }))
        ) ?? [])
      : []),
  ]
  const resultGroups = new Map<string, Array<PaletteResult>>()
  for (const result of results) {
    const group =
      result.kind === "command"
        ? result.command.group
        : result.kind === "cloud-thread"
          ? "Threads"
          : "Pull requests"
    resultGroups.set(group, [...(resultGroups.get(group) ?? []), result])
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
        to: "/agents/$threadId",
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

  const showLoading =
    ((!actionsOnly &&
      (debouncedQuery !== threadQuery || cloudThreads.isFetching)) ||
      (showPullRequests && pullRequests.isSearching)) &&
    results.length === 0
  const showError = showThreads && cloudThreads.isError && results.length === 0

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
            ref={inputRef}
            className="h-12 shrink-0 rounded-none border-0 bg-transparent px-space-4"
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
          <div className="flex shrink-0 gap-space-1 border-b border-default px-space-3 pb-space-3">
            {SCOPES.map((option) => (
              <Button
                aria-pressed={scope === option.value}
                className={cn(
                  "border-0",
                  scope === option.value
                    ? "bg-elevated-selected text-primary hover:bg-elevated-selected dark:hover:bg-elevated-selected"
                    : "text-secondary hover:text-primary"
                )}
                color="secondary"
                key={option.value}
                onClick={() => {
                  setScope(option.value)
                  inputRef.current?.focus()
                }}
                size="sm"
                variant="plain"
              >
                {option.display}
              </Button>
            ))}
          </div>
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
                {[...resultGroups].map(([group, groupResults]) => (
                  <CommandGroup heading={group} key={group}>
                    {groupResults.map((result) => {
                      const command =
                        result.kind === "command" ? result.command : null
                      const Icon =
                        result.kind === "command"
                          ? CommandIcon
                          : result.kind === "pull-request"
                            ? GitPullRequestIcon
                            : ChatCircleIcon
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
                            {result.kind === "pull-request" && (
                              <span className="ml-space-2 text-xs text-secondary">
                                {result.pr.repo} #{result.pr.number}
                                {result.pr.state && ` · ${result.pr.state}`}
                              </span>
                            )}
                          </span>
                          {result.kind === "cloud-thread" && (
                            <span className="shrink-0 text-xs text-secondary">
                              {[
                                result.thread.repoFullName,
                                formatRelativeTime(result.thread.updatedAt),
                              ]
                                .filter(Boolean)
                                .join(" · ")}
                            </span>
                          )}
                          {command?.shortcuts?.[0] && (
                            <ShortcutHint shortcut={command.shortcuts[0]} />
                          )}
                        </CommandItem>
                      )
                    })}
                  </CommandGroup>
                ))}
                {pullRequests.hasNextPage && showPullRequests && (
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
                {cloudThreads.hasNextPage && showThreads && (
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
            {query.trim() && showPullRequests && pullRequests.isError && (
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
