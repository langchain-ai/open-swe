import { useEffect, useSyncExternalStore } from "react"

export type RightPanelToggle = {
  collapsed: boolean
  toggle: () => void
}

let controller: RightPanelToggle | null = null
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/**
 * The toggle lives in the top bar, above the router outlet, so the mounted
 * thread view publishes its panel state here instead of owning a button.
 */
export function useRegisterRightPanelToggle(
  collapsed: boolean,
  onCollapsedChange: (next: boolean) => void
) {
  useEffect(() => {
    const registered: RightPanelToggle = {
      collapsed,
      toggle: () => onCollapsedChange(!collapsed),
    }
    controller = registered
    emit()
    return () => {
      if (controller !== registered) return
      controller = null
      emit()
    }
  }, [collapsed, onCollapsedChange])
}

export function useRightPanelToggle(): RightPanelToggle | null {
  return useSyncExternalStore(
    subscribe,
    () => controller,
    () => null
  )
}
