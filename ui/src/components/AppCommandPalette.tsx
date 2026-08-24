import { Dialog } from "@base-ui/react/dialog"
import { useNavigate } from "@tanstack/react-router"
import {
  Command as CommandIcon,
  Laptop,
  LoaderCircle,
  MessageSquare,
  Search,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import type { AppCommand } from "@/lib/appCommands"
import type { AgentThread } from "@/features/agents/lib/types"
import type { DesktopLocalThreadSummary } from "@/desktop"
import { Kbd } from "@/components/ui/kbd"
import { useInfiniteThreadsPages } from "@/features/agents/lib/queries"
import { useDesktopLocalThreads } from "@/features/agents/lib/desktopLocal"
import { useShortcutLabel } from "@/lib/hotkeys"
import { cn } from "@/lib/utils"
import { isLocalRuntime } from "@/lib/desktop-local-mode"

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

type PaletteResult = CommandResult | CloudThreadResult | LocalThreadResult

function ShortcutHint({ shortcut }: { shortcut: string }) {
  const label = useShortcutLabel(shortcut)
  return <Kbd className="ml-auto bg-background/70">{label}</Kbd>
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
  const commandResults: Array<CommandResult> = commands
    .filter(
      (command) =>
        command.run &&
        command.showInPalette !== false &&
        (!normalizedQuery || commandMatches(command, normalizedQuery))
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
        !normalizedQuery || thread.title.toLowerCase().includes(normalizedQuery)
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
        !normalizedQuery || thread.title.toLowerCase().includes(normalizedQuery)
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
  const [query, setQuery] = useState("")
  const [debouncedQuery, setDebouncedQuery] = useState("")
  const [activeHighlight, setActiveHighlight] = useState({ key: "", index: 0 })
  const isDesktop = isLocalRuntime()

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
  const results = useMemo(
    () =>
      buildPaletteResults(
        commands,
        cloudThreads.data?.pages.flatMap((page) => page.items) ?? [],
        localThreads.data ?? [],
        query
      ),
    [cloudThreads.data?.pages, commands, localThreads.data, query]
  )
  const resultGroups = useMemo(() => {
    const grouped = new Map<string, Array<PaletteResult>>()
    for (const result of results) {
      const group =
        result.kind === "command"
          ? result.command.group
          : result.kind === "cloud-thread"
            ? "Cloud threads"
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
    } else if (result.kind === "cloud-thread") {
      void navigate({
        to: "/agents/$threadId",
        params: { threadId: result.thread.id },
      })
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

  const showLoading = cloudThreads.isFetching && results.length === 0
  const showError = cloudThreads.isError && results.length === 0

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/45 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup
          className="fixed top-[18%] left-1/2 z-50 flex max-h-[min(34rem,70vh)] w-[min(40rem,calc(100vw-2rem))] -translate-x-1/2 flex-col overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95"
          data-hotkeys="ignore"
        >
          <Dialog.Title className="sr-only">
            Search commands and threads
          </Dialog.Title>
          <Dialog.Description className="sr-only">
            Search commands, cloud threads, and local desktop threads.
          </Dialog.Description>
          <div className="flex items-center gap-2 border-b border-border px-4">
            <Search className="size-4 shrink-0 text-muted-foreground" />
            <input
              autoFocus
              aria-activedescendant={results[activeIndex]?.id}
              aria-autocomplete="list"
              aria-controls="app-command-results"
              className="h-12 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={onInputKeyDown}
              placeholder="Search commands and threads"
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
              <div className="flex items-center justify-center gap-2 py-10 text-xs text-muted-foreground">
                <LoaderCircle className="size-4 animate-spin" />
                Searching threads…
              </div>
            ) : showError ? (
              <p className="py-10 text-center text-xs text-destructive">
                Thread search is unavailable.
              </p>
            ) : results.length === 0 ? (
              <p className="py-10 text-center text-xs text-muted-foreground">
                No commands or threads found.
              </p>
            ) : (
              <>
                {resultGroups.map(([group, groupResults]) => (
                  <div aria-label={group} key={group} role="group">
                    <div className="px-3 pt-2 pb-1 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
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
                            : MessageSquare
                      return (
                        <button
                          aria-selected={index === activeIndex}
                          className={cn(
                            "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm",
                            index === activeIndex
                              ? "bg-accent text-accent-foreground"
                              : "text-foreground"
                          )}
                          id={result.id}
                          key={result.id}
                          onClick={() => runResult(result)}
                          onMouseEnter={() => setActiveIndex(index)}
                          role="option"
                          type="button"
                        >
                          <Icon className="size-4 shrink-0 text-muted-foreground" />
                          <span className="min-w-0 flex-1 truncate">
                            {result.label}
                          </span>
                          {command?.shortcuts?.[0] && (
                            <ShortcutHint shortcut={command.shortcuts[0]} />
                          )}
                        </button>
                      )
                    })}
                  </div>
                ))}
                {cloudThreads.hasNextPage && (
                  <button
                    className="flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs text-muted-foreground hover:bg-accent hover:text-accent-foreground"
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
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
