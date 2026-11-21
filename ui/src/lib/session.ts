import { queryOptions, useQuery } from "@tanstack/react-query"
import { useHydrated } from "@tanstack/react-router"
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
 * Whether a page that requires a session must still wait for it. The server
 * renders every such page waiting, since it never asks the backend
 * (`resolveSessionOnServer`), so hydration waits too, whatever the browser
 * has resolved meanwhile.
 */
export function useSessionPending(): boolean {
  const { isLoading } = useSession()
  return !useHydrated() || isLoading
}

/**
 * Whether `owner/repo` belongs to one of the organizations this Open SWE
 * serves, where its name alone is clear enough to show.
 */
export function useIsInBaseOrg(): (repository: string) => boolean {
  const orgs = useSession().data?.base_orgs
  return (repository: string) =>
    orgs?.includes(repository.split("/")[0]?.toLowerCase() ?? "") ?? false
}
