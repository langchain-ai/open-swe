import { useCallback, useEffect, useMemo, useState } from "react"

export type PanelTabKind = "review" | "terminal" | "browser" | "files"

export interface PanelTab {
  id: string
  kind: PanelTabKind
  /** Overrides the kind's default label (terminal tabs use the shell label). */
  title?: string
  closable?: boolean
}

export interface PanelTabsState {
  tabs: Array<PanelTab>
  activeTabId: string | null
}

const STORAGE_PREFIX = "open-swe.panel-tabs.v1:"
const EMPTY: PanelTabsState = { tabs: [], activeTabId: null }
const KINDS: ReadonlyArray<PanelTabKind> = [
  "review",
  "terminal",
  "browser",
  "files",
]

/** Only terminals can be opened more than once. */
export function isMultiInstanceKind(kind: PanelTabKind): boolean {
  return kind === "terminal"
}

export function openPanelTab(
  state: PanelTabsState,
  tab: PanelTab
): PanelTabsState {
  const existing = state.tabs.find((candidate) =>
    isMultiInstanceKind(tab.kind)
      ? candidate.id === tab.id
      : candidate.kind === tab.kind
  )
  if (existing) return { ...state, activeTabId: existing.id }
  return { tabs: [...state.tabs, tab], activeTabId: tab.id }
}

export function closePanelTab(
  state: PanelTabsState,
  id: string
): PanelTabsState {
  const index = state.tabs.findIndex((tab) => tab.id === id)
  if (index < 0) return state
  const tabs = state.tabs.filter((tab) => tab.id !== id)
  return {
    tabs,
    activeTabId:
      state.activeTabId === id
        ? (tabs[Math.min(index, tabs.length - 1)]?.id ?? null)
        : state.activeTabId,
  }
}

/** Terminal tabs mirror the live terminal groups owned by `terminalState`. */
export function syncTerminalTabs(
  state: PanelTabsState,
  groupIds: ReadonlyArray<string>
): PanelTabsState {
  const live = new Set(groupIds)
  const kept = state.tabs.filter(
    (tab) => tab.kind !== "terminal" || live.has(tab.id)
  )
  const known = new Set(kept.map((tab) => tab.id))
  const added = groupIds
    .filter((id) => !known.has(id))
    .map((id): PanelTab => ({ id, kind: "terminal" }))
  if (added.length === 0 && kept.length === state.tabs.length) return state
  const tabs = [...kept, ...added]
  return {
    tabs,
    activeTabId: tabs.some((tab) => tab.id === state.activeTabId)
      ? state.activeTabId
      : (added[0]?.id ?? tabs.at(-1)?.id ?? null),
  }
}

function isPanelTab(value: unknown): value is PanelTab {
  const tab = value as PanelTab | null
  return (
    typeof tab?.id === "string" && tab.id.length > 0 && KINDS.includes(tab.kind)
  )
}

export function readPanelTabs(sessionId: string): PanelTabsState {
  if (typeof window === "undefined") return EMPTY
  const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${sessionId}`)
  if (!raw) return EMPTY
  try {
    const parsed = JSON.parse(raw) as Partial<PanelTabsState>
    const tabs = (Array.isArray(parsed.tabs) ? parsed.tabs : []).filter(
      isPanelTab
    )
    return {
      tabs,
      activeTabId:
        tabs.find((tab) => tab.id === parsed.activeTabId)?.id ??
        tabs[0]?.id ??
        null,
    }
  } catch {
    return EMPTY
  }
}

export function writePanelTabs(sessionId: string, state: PanelTabsState): void {
  if (typeof window === "undefined") return
  window.localStorage.setItem(
    `${STORAGE_PREFIX}${sessionId}`,
    JSON.stringify(state)
  )
}

export function usePanelTabs(sessionId: string) {
  const [state, setState] = useState<PanelTabsState>(() =>
    readPanelTabs(sessionId)
  )

  useEffect(() => setState(readPanelTabs(sessionId)), [sessionId])

  const update = useCallback(
    (change: (current: PanelTabsState) => PanelTabsState) => {
      setState((current) => {
        const next = change(current)
        if (next !== current) writePanelTabs(sessionId, next)
        return next
      })
    },
    [sessionId]
  )

  return useMemo(
    () => ({
      tabs: state.tabs,
      activeTabId: state.activeTabId,
      activeTab: state.tabs.find((tab) => tab.id === state.activeTabId) ?? null,
      open: (tab: PanelTab) => update((current) => openPanelTab(current, tab)),
      select: (id: string) =>
        update((current) => ({ ...current, activeTabId: id })),
      close: (id: string) => update((current) => closePanelTab(current, id)),
      syncTerminals: (groupIds: ReadonlyArray<string>) =>
        update((current) => syncTerminalTabs(current, groupIds)),
    }),
    [state, update]
  )
}
