import { useEffect, useState } from "react"

import { dashboardApiUrl } from "@/lib/dashboard-fetch"

export type AiSearchStatus = "idle" | "streaming" | "done" | "error"

export interface AiSearchState<T> {
  /** Every match so far, most relevant first; re-ranked as each batch lands. */
  matches: Array<T>
  /** How many candidates the server has judged, newest first. */
  scanned: number
  status: AiSearchStatus
}

interface StreamState<T> extends AiSearchState<T> {
  path: string | null
}

const IDLE: AiSearchState<never> = { matches: [], scanned: 0, status: "idle" }
const STARTING: AiSearchState<never> = {
  matches: [],
  scanned: 0,
  status: "streaming",
}

/**
 * Follow a progressive AI search: the server judges candidates a page at a
 * time and sends each page's matches as a `batch` frame, then `done` or
 * `failed`. Changing `path`, or passing `null`, closes the stream, which
 * cancels the search on the server. `EventSource` carries the session cookie
 * and nothing else, and its own retry would restart the search, so it is
 * closed on any end or error. `key` identifies a match across batches; pass a
 * stable (module-level) function, since a new one restarts the search.
 */
export function useAiSearchStream<T extends { relevance: number }>(
  path: string | null,
  key: (match: T) => string
): AiSearchState<T> {
  const [state, setState] = useState<StreamState<T>>({ ...IDLE, path: null })

  useEffect(() => {
    if (!path) return
    const source = new EventSource(dashboardApiUrl(path), {
      withCredentials: true,
    })
    const update = (apply: (current: StreamState<T>) => StreamState<T>) =>
      setState((previous) =>
        apply(previous.path === path ? previous : { ...STARTING, path })
      )
    const finish = (status: AiSearchStatus) => {
      source.close()
      update((current) => ({ ...current, status }))
    }
    source.addEventListener("batch", (event) => {
      let batch: { matches: Array<T>; scanned: number }
      try {
        batch = JSON.parse(event.data) as typeof batch
      } catch {
        finish("error")
        return
      }
      update((current) => ({
        ...current,
        matches: rerank(current.matches, batch.matches, key),
        scanned: batch.scanned,
      }))
    })
    source.addEventListener("done", () => finish("done"))
    source.addEventListener("failed", () => finish("error"))
    source.addEventListener("error", () => finish("error"))
    return () => source.close()
  }, [key, path])

  if (!path) return IDLE
  return state.path === path ? state : STARTING
}

function rerank<T extends { relevance: number }>(
  current: Array<T>,
  incoming: Array<T>,
  key: (match: T) => string
): Array<T> {
  const byKey = new Map(current.map((match) => [key(match), match]))
  for (const match of incoming) byKey.set(key(match), match)
  return [...byKey.values()].sort((a, b) => b.relevance - a.relevance)
}
