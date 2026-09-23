import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query"

import { reportError } from "@/lib/errorReporting"

type DashboardMutationMeta = {
  /** Toast title when the mutation fails, e.g. "Couldn't pin thread". */
  errorTitle?: string
  /** The caller shows this failure inline instead of as a toast. */
  silent?: boolean
}

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: DashboardMutationMeta
  }
}

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
        if (mutation.meta?.silent) return
        const key = mutation.options.mutationKey
        reportError({
          title: mutation.meta?.errorTitle ?? "Something went wrong",
          error,
          mutation: key ? JSON.stringify(key) : undefined,
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
