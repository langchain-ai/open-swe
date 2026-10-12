import { useQuery } from "@tanstack/react-query"

import { useSession } from "@/lib/session"

import { pullRequestStatusQuery } from "./cache"

/** The same live PR state the PR list shows, from the same cache entry. */
export function usePullRequestStatus(repo: string, number: number) {
  const session = useSession()
  const login = session.data?.login
  return useQuery({
    ...pullRequestStatusQuery(login ?? "", { repo, number }),
    enabled: !!login,
    refetchOnWindowFocus: false,
  })
}
