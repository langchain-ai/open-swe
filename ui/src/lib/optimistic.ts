import { useMutationState } from "@tanstack/react-query"
import type { MutationKey, QueryClient, QueryKey } from "@tanstack/react-query"

/**
 * Variables of every pending mutation under `mutationKey`. A mutation's own
 * `variables` holds only its latest call, so a per-row lock built on it
 * unlocks earlier rows that are still saving.
 */
export function usePendingVariables<TVariables>(
  mutationKey: MutationKey
): Array<TVariables> {
  return useMutationState({
    filters: { mutationKey, status: "pending" },
    select: (mutation) => mutation.state.variables as TVariables,
  })
}

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
