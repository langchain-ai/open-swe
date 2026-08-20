import { useCallback, useEffect, useMemo, useState } from "react"

export type PanelTabKind =
  "changes" | "terminal" | "files" | "file" | "browser" | "pull-request"

export interface PanelTab {
  id: string
  kind: PanelTabKind
  title?: string
  closable?: boolean
  resourceId?: string
}

export interface PanelTabsState {
  tabs: Array<PanelTab>
  activeTabId: string | null
}

const STORAGE_PREFIX = "open-swe.panel-tabs.v1:"
const EMPTY: PanelTabsState = { tabs: [], activeTabId: null }
const KINDS: ReadonlyArray<PanelTabKind> = [
  "changes",
  "terminal",
  "files",
  "file",
  "browser",
  "pull-request",
]

export const CHANGES_TAB: PanelTab = {
  id: "changes",
  kind: "changes",
  title: "Changes",
  closable: true,
}
export const FILES_TAB: PanelTab = {
  id: "files",
  kind: "files",
  title: "Files",
}
export const BROWSER_TAB: PanelTab = {
  id: "browser:new",
  kind: "browser",
  title: "Browser",
}
export const PULL_REQUEST_TAB: PanelTab = {
  id: "pull-request",
  kind: "pull-request",
  title: "Pull request",
}
export const AGENT_COMMON_TABS: ReadonlyArray<PanelTab> = []
export const AGENT_PANEL_KINDS: ReadonlyArray<PanelTabKind> = [
  "browser",
  "terminal",
  "files",
  "changes",
  "pull-request",
]

export function isMultiInstanceKind(kind: PanelTabKind): boolean {
  return kind === "terminal" || kind === "file" || kind === "browser"
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
  if (index < 0 || state.tabs[index]?.closable === false) return state
  const tabs = state.tabs.filter((tab) => tab.id !== id)
  return {
    tabs,
    activeTabId:
      state.activeTabId === id
        ? (tabs[Math.min(index, tabs.length - 1)]?.id ?? null)
        : state.activeTabId,
  }
}

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

function migratePanelTab(value: unknown): PanelTab | null {
  const tab = value as PanelTab | null
  if (typeof tab?.id !== "string" || tab.id.length === 0) return null
  const kind = (tab.kind as string) === "review" ? "changes" : tab.kind
  const id = tab.id === "review" ? "changes" : tab.id
  if (!KINDS.includes(kind)) return null
  return { ...tab, id, kind }
}

function normalizePanelTabs(
  state: PanelTabsState,
  commonTabs: ReadonlyArray<PanelTab>
): PanelTabsState {
  const tabs = [...commonTabs]
  for (const tab of state.tabs) {
    if (!tabs.some((candidate) => candidate.id === tab.id)) tabs.push(tab)
  }
  const activeTabId =
    state.activeTabId === "review" ? "changes" : state.activeTabId
  return {
    tabs,
    activeTabId: tabs.some((tab) => tab.id === activeTabId)
      ? activeTabId
      : (tabs[0]?.id ?? null),
  }
}

export function readPanelTabs(
  sessionId: string,
  commonTabs: ReadonlyArray<PanelTab> = []
): PanelTabsState {
  if (typeof window === "undefined")
    return normalizePanelTabs(EMPTY, commonTabs)
  const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${sessionId}`)
  if (!raw) return normalizePanelTabs(EMPTY, commonTabs)
  try {
    const parsed = JSON.parse(raw) as Partial<PanelTabsState>
    const tabs = (Array.isArray(parsed.tabs) ? parsed.tabs : [])
      .map(migratePanelTab)
      .filter((tab): tab is PanelTab => tab !== null)
    const state = normalizePanelTabs(
      {
        tabs,
        activeTabId:
          typeof parsed.activeTabId === "string" ? parsed.activeTabId : null,
      },
      commonTabs
    )
    writePanelTabs(sessionId, state)
    return state
  } catch {
    return normalizePanelTabs(EMPTY, commonTabs)
  }
}

export function writePanelTabs(sessionId: string, state: PanelTabsState): void {
  if (typeof window === "undefined") return
  window.localStorage.setItem(
    `${STORAGE_PREFIX}${sessionId}`,
    JSON.stringify(state)
  )
}

export function usePanelTabs(
  sessionId: string,
  commonTabs: ReadonlyArray<PanelTab> = []
) {
  const [state, setState] = useState<PanelTabsState>(() =>
    readPanelTabs(sessionId, commonTabs)
  )

  useEffect(
    () => setState(readPanelTabs(sessionId, commonTabs)),
    [commonTabs, sessionId]
  )

  const update = useCallback(
    (change: (current: PanelTabsState) => PanelTabsState) => {
      setState((current) => {
        const next = normalizePanelTabs(change(current), commonTabs)
        writePanelTabs(sessionId, next)
        return next
      })
    },
    [commonTabs, sessionId]
  )

  const open = useCallback(
    (tab: PanelTab) => update((current) => openPanelTab(current, tab)),
    [update]
  )
  const select = useCallback(
    (id: string) =>
      update((current) =>
        current.tabs.some((tab) => tab.id === id)
          ? { ...current, activeTabId: id }
          : current
      ),
    [update]
  )
  const openChanges = useCallback(() => open(CHANGES_TAB), [open])
  const openFiles = useCallback(() => open(FILES_TAB), [open])
  const openBrowser = useCallback(() => open(BROWSER_TAB), [open])
  const openFile = useCallback(
    (path: string) =>
      open({
        id: `file:${path}`,
        kind: "file",
        title: path.slice(path.lastIndexOf("/") + 1),
        resourceId: path,
      }),
    [open]
  )
  const close = useCallback(
    (id: string) => update((current) => closePanelTab(current, id)),
    [update]
  )
  const syncTerminals = useCallback(
    (groupIds: ReadonlyArray<string>) =>
      update((current) => syncTerminalTabs(current, groupIds)),
    [update]
  )

  return useMemo(
    () => ({
      tabs: state.tabs,
      activeTabId: state.activeTabId,
      activeTab: state.tabs.find((tab) => tab.id === state.activeTabId) ?? null,
      open,
      select,
      openChanges,
      openFiles,
      openBrowser,
      openFile,
      close,
      syncTerminals,
    }),
    [close, open, openChanges, select, state, syncTerminals]
  )
}
