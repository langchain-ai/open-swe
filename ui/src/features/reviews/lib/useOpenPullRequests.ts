import { useInfiniteQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import type { ReviewSort } from "../search"

export function useOpenPullRequests(
  login: string,
  repo: string[],
  sort: ReviewSort,
  direction: "asc" | "desc"
) {
  return useInfiniteQuery({
    queryKey: ["my-pull-requests", login, repo, sort, direction],
    queryFn: ({ pageParam }) =>
      api.myPullRequests(repo.join(","), sort, direction, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last) => last.nextPage ?? undefined,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
}
