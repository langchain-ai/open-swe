/**
 * React bindings: the host in context, its live context as state, and the
 * dashboard's appearance applied to this frame.
 */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react"

import type { PluginHost } from "./client"
import type {
  HostContext,
  Theme,
  ThreadListOptions,
  ThreadSummary,
} from "./protocol"

const HostContextValue = createContext<PluginHost | null>(null)

export function PluginHostProvider({
  host,
  children,
}: {
  host: PluginHost
  children: ReactNode
}) {
  return <HostContextValue value={host}>{children}</HostContextValue>
}

export function usePluginHost(): PluginHost {
  const host = useContext(HostContextValue)
  if (!host) throw new Error("usePluginHost needs a PluginHostProvider")
  return host
}

/** The dashboard's context, re-rendered as its theme and path change. */
export function useHostContext(): HostContext {
  const host = usePluginHost()
  const [context, setContext] = useState(host.context)
  useEffect(() => host.on(() => setContext(host.context)), [host])
  return context
}

/**
 * Matches the dashboard's light or dark mode: Macaw reads `html.dark`, and
 * `color-scheme` styles the browser's own controls and scrollbars.
 */
export function applyTheme(theme: Theme, root = document.documentElement) {
  root.classList.toggle("dark", theme === "dark")
  root.dataset.theme = theme
  root.style.colorScheme = theme
}

/** Keeps this frame's theme on the dashboard's for as long as it is mounted. */
export function useDashboardTheme(): Theme {
  const { theme } = useHostContext()
  useEffect(() => applyTheme(theme), [theme])
  return theme
}

function failureText(failure: unknown): string {
  return failure instanceof Error ? failure.message : String(failure)
}

/** `first`, then those of `rest` it doesn't already hold. */
function mergeThreads(
  first: ThreadSummary[],
  rest: ThreadSummary[]
): ThreadSummary[] {
  const ids = new Set(first.map((thread) => thread.threadId))
  return [...first, ...rest.filter((thread) => !ids.has(thread.threadId))]
}

/**
 * The threads `options` lists, newest activity first, for an app with
 * `threads:read`. The first page refreshes every `refreshMs`; `loadMore` adds
 * the next one while `hasMore`.
 */
export function useThreads(
  options: Omit<ThreadListOptions, "offset">,
  refreshMs = 15_000
): {
  threads: ThreadSummary[] | null
  error: string | null
  hasMore: boolean
  loadMore: () => void
} {
  const host = usePluginHost()
  const { source, bot, limit } = options
  // Keyed by the options, so other options start from an empty list.
  const key = JSON.stringify([source, bot ?? null, limit ?? null])
  const [list, setList] = useState<{
    key: string
    threads: ThreadSummary[]
    next: number | null
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const current = list?.key === key ? list : null

  useEffect(() => {
    let live = true
    const refresh = async () => {
      try {
        const page = await host.threads.list({ source, bot, limit })
        if (!live) return
        setList((prev) =>
          prev?.key === key
            ? { ...prev, threads: mergeThreads(page.threads, prev.threads) }
            : { key, threads: page.threads, next: page.nextOffset }
        )
        setError(null)
      } catch (failure) {
        if (live) setError(failureText(failure))
      }
    }
    void refresh()
    const timer = window.setInterval(refresh, refreshMs)
    return () => {
      live = false
      window.clearInterval(timer)
    }
  }, [host, key, source, bot, limit, refreshMs])

  const loadMore = () => {
    const next = current?.next
    if (next === null || next === undefined) return
    host.threads.list({ source, bot, limit, offset: next }).then(
      (page) =>
        setList((prev) =>
          prev?.key === key
            ? {
                key,
                threads: mergeThreads(prev.threads, page.threads),
                next: page.nextOffset,
              }
            : prev
        ),
      (failure: unknown) => setError(failureText(failure))
    )
  }

  return {
    threads: current?.threads ?? null,
    error,
    hasMore: current !== null && current.next !== null,
    loadMore,
  }
}

/**
 * The thread the app's path names (`/<threadId>`): from `threads` when it is
 * there, else read on its own. With no path it is the first of `threads`;
 * `missing` when the path names one the person can't read. `open` sets the path.
 */
export function useOpenThread(threads: ThreadSummary[] | null): {
  thread: ThreadSummary | null
  missing: boolean
  open: (threadId: string | null) => void
} {
  const host = usePluginHost()
  const id = useHostContext().path.slice(1) || null
  const listed = id
    ? threads?.find((thread) => thread.threadId === id)
    : undefined
  const needsRead = id !== null && threads !== null && listed === undefined
  const [read, setRead] = useState<{
    id: string
    thread: ThreadSummary | null
  } | null>(null)

  useEffect(() => {
    if (!needsRead || id === null) return
    let live = true
    host.threads.get(id).then(
      (thread) => live && setRead({ id, thread }),
      () => live && setRead({ id, thread: null })
    )
    return () => {
      live = false
    }
  }, [host, id, needsRead])

  const own = read !== null && read.id === id ? read : null
  return {
    thread:
      id === null ? (threads?.[0] ?? null) : (listed ?? own?.thread ?? null),
    missing: needsRead && own !== null && own.thread === null,
    open: (threadId) => void host.nav.setPath(threadId ? `/${threadId}` : "/"),
  }
}
