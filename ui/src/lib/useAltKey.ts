import { useSyncExternalStore } from "react"

let held = false
const listeners = new Set<() => void>()

function set(next: boolean) {
  if (next === held) return
  held = next
  for (const listener of listeners) listener()
}

const track = (event: KeyboardEvent | PointerEvent) => set(event.altKey)
const release = () => set(false)

/** One set of window listeners however many rows ask. */
function subscribe(listener: () => void) {
  if (listeners.size === 0) {
    window.addEventListener("keydown", track, true)
    window.addEventListener("keyup", track, true)
    window.addEventListener("pointermove", track, true)
    window.addEventListener("blur", release)
  }
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
    if (listeners.size > 0) return
    window.removeEventListener("keydown", track, true)
    window.removeEventListener("keyup", track, true)
    window.removeEventListener("pointermove", track, true)
    window.removeEventListener("blur", release)
    held = false
  }
}

/** Whether Alt (Option on a Mac) is held right now, for actions it switches. */
export function useAltKey(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => held,
    () => false
  )
}
