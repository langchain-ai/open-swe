import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

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
        console.error("Mutation failed", {
          mutationKey: mutation.options.mutationKey,
          error,
        })
        // Mutations with their own onError already tell the user what failed.
        if (!mutation.options.onError)
          toast.error("Something went wrong", { description: error.message })
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
