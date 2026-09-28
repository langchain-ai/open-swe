/**
 * Where a desktop browser tab should paint. The right panel renders an empty
 * slot for the active tab and publishes its rectangle here; the root-level
 * webview host reads it and positions the `<webview>` over it with `fixed`
 * positioning, so switching threads or panels never re-parents (and so never
 * reloads) the page.
 */
import { create } from "zustand"

export interface BrowserSurfaceRect {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

export interface BrowserSurfacePresentation {
  readonly rect: BrowserSurfaceRect | null
  readonly visible: boolean
  readonly zIndex: number
  readonly owner: symbol | null
}

interface BrowserSurfaceStoreState {
  readonly byTabId: Record<string, BrowserSurfacePresentation>
  readonly claim: (tabId: string, owner: symbol) => void
  readonly present: (
    tabId: string,
    owner: symbol,
    rect: BrowserSurfaceRect,
    visible: boolean,
    zIndex: number
  ) => void
  readonly release: (tabId: string, owner: symbol) => void
}

export interface BrowserSurfaceLease {
  readonly present: (
    rect: BrowserSurfaceRect,
    visible: boolean,
    zIndex?: number
  ) => boolean
  readonly release: () => void
}

const rectEquals = (
  left: BrowserSurfaceRect | null,
  right: BrowserSurfaceRect
): boolean =>
  left !== null &&
  left.x === right.x &&
  left.y === right.y &&
  left.width === right.width &&
  left.height === right.height

export const useBrowserSurfaceStore = create<BrowserSurfaceStoreState>()(
  (set) => ({
    byTabId: {},
    claim: (tabId, owner) =>
      set((state) => {
        const current = state.byTabId[tabId]
        if (current?.owner === owner) return state
        return {
          byTabId: {
            ...state.byTabId,
            [tabId]: {
              rect: current?.rect ?? null,
              visible: false,
              zIndex: current?.zIndex ?? 30,
              owner,
            },
          },
        }
      }),
    present: (tabId, owner, rect, visible, zIndex) =>
      set((state) => {
        const current = state.byTabId[tabId]
        if (current?.owner !== owner) return state
        if (
          current.visible === visible &&
          current.zIndex === zIndex &&
          rectEquals(current.rect, rect)
        ) {
          return state
        }
        return {
          byTabId: {
            ...state.byTabId,
            [tabId]: { ...current, rect, visible, zIndex },
          },
        }
      }),
    release: (tabId, owner) =>
      set((state) => {
        const current = state.byTabId[tabId]
        if (current?.owner !== owner) return state
        return {
          byTabId: {
            ...state.byTabId,
            [tabId]: { ...current, visible: false, owner: null },
          },
        }
      }),
  })
)

/**
 * Claims the surface for one slot. The most recent claimant wins, so a stale
 * slot (one that is unmounting) can no longer move the webview; its `present`
 * calls return false.
 */
export function acquireBrowserSurface(tabId: string): BrowserSurfaceLease {
  const owner = Symbol(`browser-surface:${tabId}`)
  let released = false
  useBrowserSurfaceStore.getState().claim(tabId, owner)
  return {
    present: (rect, visible, zIndex = 30) => {
      if (released) return false
      const state = useBrowserSurfaceStore.getState()
      if (state.byTabId[tabId]?.owner !== owner) return false
      state.present(tabId, owner, rect, visible, zIndex)
      return true
    },
    release: () => {
      if (released) return
      released = true
      useBrowserSurfaceStore.getState().release(tabId, owner)
    },
  }
}
