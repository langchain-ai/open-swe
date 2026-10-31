import { queryOptions, useQuery } from "@tanstack/react-query"
import { useCallback } from "react"
import { ApiError, api } from "./api"
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

/**
 * Whether `owner/repo` belongs to one of the organizations this Open SWE
 * serves, where its name alone is clear enough to show.
 */
export function useIsInBaseOrg(): (repository: string) => boolean {
  const orgs = useSession().data?.base_orgs
  return useCallback(
    (repository: string) =>
      orgs?.includes(repository.split("/")[0]?.toLowerCase() ?? "") ?? false,
    [orgs]
  )
}
