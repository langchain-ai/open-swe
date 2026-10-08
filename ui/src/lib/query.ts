import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query"

import { reportError } from "@/lib/errorReporting"
import type { InvalidationTopic } from "@/lib/invalidations/topics"

type DashboardMutationMeta = {
  /** Toast title when the mutation fails, e.g. "Couldn't pin thread". */
  errorTitle?: string
  /** The caller shows this failure inline, so skip the toast; it is still logged. */
  silent?: boolean
}

type DashboardQueryMeta = Record<string, unknown> & {
  /** Topics whose invalidation refetches this query; see `lib/invalidations/topics.ts`. */
  invalidatedBy?: readonly InvalidationTopic[]
}

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: DashboardMutationMeta
    queryMeta: DashboardQueryMeta
  }
}

export const BROWSER_CACHE_MAX_AGE_MS = 10 * 60_000

/** Data the browser keeps must still expire, even while it stays on screen. */
export const expiresInBrowser = {
  staleTime: BROWSER_CACHE_MAX_AGE_MS,
  refetchInterval: BROWSER_CACHE_MAX_AGE_MS,
} as const

export function makeQueryClient() {
  return new QueryClient({
    queryCache: new QueryCache({
      onSuccess: (_data, query) => {
        // Queries that surface a retained refresh failure clear it here, so
        // automatic interval/focus fetches recover it, not only manual ones.
        const onRefreshErrorChange = query.meta?.onRefreshErrorChange
        if (typeof onRefreshErrorChange === "function") {
          ;(onRefreshErrorChange as (error: null) => void)(null)
        }
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        const key = mutation.options.mutationKey
        reportError({
          title: mutation.meta?.errorTitle ?? "Something went wrong",
          error,
          mutation: key ? JSON.stringify(key) : undefined,
          showToast: !mutation.meta?.silent,
        })
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
    },
  })
}
