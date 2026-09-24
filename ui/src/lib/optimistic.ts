import type { QueryClient, QueryKey } from "@tanstack/react-query"

/**
 * Applies `update` to the cached data at `queryKey` and returns its undo.
 *
 * The undo restores the snapshot only while the cache still holds this
 * update's result; once another write has landed on top, restoring would
 * revert that write too, so it refetches instead.
 */
export async function optimisticUpdate<T>(
  queryClient: QueryClient,
  queryKey: QueryKey,
  update: (previous: T) => T
): Promise<() => void> {
  await queryClient.cancelQueries({ queryKey, exact: true })
  const previous = queryClient.getQueryData<T>(queryKey)
  if (previous === undefined) return () => {}
  const applied = queryClient.setQueryData<T>(queryKey, update(previous))
  return () => {
    if (queryClient.getQueryData<T>(queryKey) === applied) {
      queryClient.setQueryData<T>(queryKey, previous)
      return
    }
    void queryClient.invalidateQueries({ queryKey, exact: true })
  }
}
