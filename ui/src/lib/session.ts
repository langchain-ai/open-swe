import { queryOptions, useQuery } from "@tanstack/react-query"
import { api } from "./api"
import { ApiError } from "./apiClient"
import type { SessionUser } from "./api"

export const sessionQueryOptions = queryOptions<SessionUser | null>({
  queryKey: ["session"],
  queryFn: async () => {
    try {
      return await api.me()
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return null
      throw e
    }
  },
  staleTime: 60_000,
  retry: false,
})

export function useSession() {
  return useQuery(sessionQueryOptions)
}
