import { useCallback, useSyncExternalStore } from "react"

import type { PanelThreadRef } from "@/features/agents/lib/rightPanelStore"
import {
  type BrowserTabController,
  browserControllerKey,
  getBrowserController,
  subscribeBrowserController,
} from "@/features/agents/browser/browserController"

/** The live controller for a tab, re-rendering when a host attaches or detaches. */
export function useBrowserController(
  ref: PanelThreadRef,
  tabId: string | null
): BrowserTabController | null {
  const key = tabId ? browserControllerKey(ref, tabId) : null
  const subscribe = useCallback(
    (listener: () => void) =>
      key ? subscribeBrowserController(key, listener) : () => {},
    [key]
  )
  const getSnapshot = useCallback(
    () => (key ? getBrowserController(key) : null),
    [key]
  )
  return useSyncExternalStore(subscribe, getSnapshot, () => null)
}
