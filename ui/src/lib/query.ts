import { QueryCache, QueryClient } from "@tanstack/react-query"

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
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
    },
  })
}
