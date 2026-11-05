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
import { useEffect, useState } from "react"

import type { AppCommand } from "@/lib/appCommands"
import type { PullRequestSearchResult } from "@/lib/api"
import { usePullRequestSearch } from "@/features/reviews/lib/usePullRequestSearch"
import type { AgentThread } from "@/features/agents/lib/types"
import { useInfiniteThreadsPages } from "@/features/agents/lib/queries"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import { parsePullRequestReference } from "@/features/reviews/search"
import { useShortcutLabel } from "@/lib/hotkeys"
import { formatRelativeTime } from "@/lib/utils"

interface ResultDetails {
  /** Supporting facts shown after the label, from data already in Postgres. */
  details?: Array<string>
  updatedAt?: number
}

interface CommandResult extends ResultDetails {
  id: string
  kind: "command"
  label: string
  command: AppCommand
}

interface CloudThreadResult extends ResultDetails {
  id: string
  kind: "cloud-thread"
  label: string
  thread: AgentThread
}

interface PullRequestResult extends ResultDetails {
  id: string
  kind: "pull-request"
  label: string
  pr: Pick<PullRequestSearchResult, "repo" | "number">
}

const THREAD_STATUS_LABELS: Partial<Record<AgentThread["status"], string>> = {
  running: "Running",
  interrupted: "Needs input",
  error: "Error",
}

const PR_STATE_LABELS: Record<PullRequestSearchResult["state"], string> = {
  open: "Open",
  draft: "Draft",
  merged: "Merged",
  closed: "Closed",
}

function threadResult(thread: AgentThread): CloudThreadResult {
  const status = thread.resolved
    ? "Resolved"
    : THREAD_STATUS_LABELS[thread.status]
  return {
    id: `cloud:${thread.id}`,
    kind: "cloud-thread",
    label: thread.title,
    thread,
    details: [
      thread.repoFullName || thread.repo,
      status,
      thread.pr && `#${thread.pr.number} ${PR_STATE_LABELS[thread.pr.state]}`,
      thread.ownerLogin && `@${thread.ownerLogin}`,
    ].filter((detail): detail is string => Boolean(detail)),
    updatedAt: thread.updatedAt,
  }
}

function pullRequestResult(pr: PullRequestSearchResult): PullRequestResult {
  const findings = pr.review_findings
  return {
    id: `pr:${pr.repo}#${pr.number}`,
    kind: "pull-request",
    label: pr.title || `${pr.repo} #${pr.number}`,
    pr,
    details: [
      `${pr.repo} #${pr.number}`,
      PR_STATE_LABELS[pr.state],
      pr.author && `@${pr.author}`,
      pr.additions != null &&
        pr.deletions != null &&
        `+${pr.additions} −${pr.deletions}`,
      findings != null &&
        `${findings} ${findings === 1 ? "finding" : "findings"}`,
    ].filter((detail): detail is string => Boolean(detail)),
    updatedAt: pr.updated_at ? Date.parse(pr.updated_at) : undefined,
  }
}

type PaletteResult = CommandResult | CloudThreadResult | PullRequestResult

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
  const cloudResults = actionsOnly ? [] : cloudThreads.map(threadResult)
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

  const actionsOnly = query.trim().startsWith(">")
  const prReference = actionsOnly ? null : parsePullRequestReference(query)
  const threadQuery = prReference
    ? `https://github.com/${prReference.owner}/${prReference.repo}/pull/${prReference.number}`
    : query.trim()

  useEffect(() => {
    if (!open) {
      // oxlint-disable-next-line react/set-state-in-effect
      setQuery("")
      setDebouncedQuery("")
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
    { enabled: open && !actionsOnly, staleWhileRevalidate: true }
  )
  const pullRequests = usePullRequestSearch(threadQuery, {
    enabled: open && !actionsOnly,
    includeRecent: true,
  })
  const pullRequestResults =
    pullRequests.data?.pages.flatMap((page) =>
      page.pull_requests.map(pullRequestResult)
    ) ?? []
  const referencedPullRequest: Array<PullRequestResult> =
    prReference && pullRequestResults.length === 0
      ? [
          {
            id: `pr:${prReference.owner}/${prReference.repo}#${prReference.number}`,
            kind: "pull-request",
            label: "Open Review Page",
            pr: {
              repo: `${prReference.owner}/${prReference.repo}`,
              number: prReference.number,
            },
            details: [
              `${prReference.owner}/${prReference.repo} #${prReference.number}`,
            ],
          },
        ]
      : []
  const results: Array<PaletteResult> = [
    ...buildPaletteResults(
      commands,
      debouncedQuery === threadQuery
        ? (cloudThreads.data?.pages.flatMap((page) => page.items) ?? [])
        : [],
      query
    ),
    ...(actionsOnly ? [] : [...referencedPullRequest, ...pullRequestResults]),
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
      pullRequests.isSearching) &&
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
                          <span className="flex min-w-0 flex-1 flex-col">
                            <span className="truncate">{result.label}</span>
                            {result.details?.length ? (
                              <span className="truncate text-xs text-secondary">
                                {result.details.join(" · ")}
                              </span>
                            ) : null}
                          </span>
                          {result.updatedAt ? (
                            <span className="shrink-0 text-xs text-tertiary">
                              {formatRelativeTime(result.updatedAt)}
                            </span>
                          ) : null}
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
            {!actionsOnly && pullRequests.isError && (
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
