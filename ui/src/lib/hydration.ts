import { useSyncExternalStore } from "react"

const noopSubscribe = () => () => {}

/** False through SSR and the first client render, true once hydrated. */
export function useIsHydrated(): boolean {
  return useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false
  )
}
