import { useInfiniteQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { api } from "@/lib/api"
import { useSession } from "@/lib/session"

/** Searches stored pull requests; `includeRecent` lists the latest for an empty query. */
export function usePullRequestSearch(
  query: string,
  { enabled = true, includeRecent = false } = {}
) {
  const { data: session } = useSession()
  const [debounced, setDebounced] = useState(query.trim())
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 180)
    return () => window.clearTimeout(timer)
  }, [query])
  const search = useInfiniteQuery({
    queryKey: ["pull-request-search", session?.login, debounced],
    queryFn: ({ pageParam }) => api.searchPullRequests(debounced, pageParam),
    initialPageParam: 0,
    getNextPageParam: (last, pages) =>
      last.has_more
        ? pages.reduce((total, page) => total + page.pull_requests.length, 0)
        : undefined,
    enabled:
      enabled &&
      Boolean(session?.login) &&
      (includeRecent || Boolean(debounced)),
    staleTime: 30_000,
    retry: false,
  })
  return {
    fetchNextPage: search.fetchNextPage,
    hasNextPage: debounced === query.trim() && search.hasNextPage,
    isFetching: search.isFetching,
    isFetchingNextPage: search.isFetchingNextPage,
    isError: debounced === query.trim() && search.isError,
    data: debounced === query.trim() ? search.data : undefined,
    isSearching:
      enabled &&
      (includeRecent || Boolean(query.trim())) &&
      (debounced !== query.trim() || search.isFetching),
  }
}
