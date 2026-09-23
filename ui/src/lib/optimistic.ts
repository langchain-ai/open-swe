import type { QueryClient, QueryKey } from "@tanstack/react-query"

/** Applies `update` to the cached data at `queryKey` and returns its undo. */
export async function optimisticUpdate<T>(
  queryClient: QueryClient,
  queryKey: QueryKey,
  update: (previous: T) => T
): Promise<() => void> {
  await queryClient.cancelQueries({ queryKey, exact: true })
  const previous = queryClient.getQueryData<T>(queryKey)
  if (previous !== undefined)
    queryClient.setQueryData<T>(queryKey, update(previous))
  return () => queryClient.setQueryData<T>(queryKey, previous)
}
