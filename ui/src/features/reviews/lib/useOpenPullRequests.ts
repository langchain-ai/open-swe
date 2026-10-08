import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { expiresInBrowser } from "@/lib/query"
import type { ReviewSort } from "../search"

export function useOpenPullRequests(
  login: string,
  repo: string[],
  sort: ReviewSort,
  direction: "asc" | "desc",
  scope: "mine" | "review-assigned" | "review-requested"
) {
  return useInfiniteQuery({
    queryKey: ["open-pull-requests", login, scope, repo, sort, direction],
    queryFn: ({ pageParam }) =>
      api.openPullRequests(repo.join(","), sort, direction, pageParam, scope),
    initialPageParam: 1,
    getNextPageParam: (last) => last.nextPage ?? undefined,
    // Re-sorting keeps the previous snapshot on screen: blanking the list and
    // growing it back shifts every row under the pointer.
    placeholderData: keepPreviousData,
    ...expiresInBrowser,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
}
