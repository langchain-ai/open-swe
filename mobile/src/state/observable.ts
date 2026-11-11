import { useSyncExternalStore } from "react"

/** State that tells subscribed views when it changes. */
export class Observable {
  private listeners = new Set<() => void>()
  private revision = 0

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  getRevision = () => this.revision

  protected changed() {
    this.revision += 1
    for (const listener of this.listeners) listener()
  }
}

/** Re-render whenever `observable` changes. */
export function useObserve<T extends Observable | null | undefined>(
  observable: T
): T {
  useSyncExternalStore(
    observable?.subscribe ?? noopSubscribe,
    observable?.getRevision ?? zero
  )
  return observable
}

const noopSubscribe = () => () => {}
const zero = () => 0
