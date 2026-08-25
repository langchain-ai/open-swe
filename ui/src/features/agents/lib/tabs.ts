import { useEffect, useSyncExternalStore } from "react"

/**
 * Open SWE is a tabbed app: the shell keeps one strip of tabs across the top
 * and the home screen (no tab) lists every session. A tab is just a route the
 * user parked — the router still owns navigation, this store only remembers
 * which routes are parked and in what order.
 */
export type AgentTabKind = "cloud" | "local" | "new"

export interface AgentTab {
  /** Stable per-route id, e.g. `cloud:<threadId>` — also the storage key. */
  id: string
  kind: AgentTabKind
  /** Internal path under `/agents`. */
  path: string
  title: string
}

export interface AgentTabsState {
  tabs: Array<AgentTab>
  /** `null` while the home screen is showing. */
  activeId: string | null
}

export const HOME_PATH = "/agents"
export const NEW_TAB_PATH = "/agents/new"
const STORAGE_KEY = "open-swe.agents.tabs"
const MAX_TABS = 30
const KINDS: ReadonlyArray<AgentTabKind> = ["cloud", "local", "new"]

export const EMPTY_TABS_STATE: AgentTabsState = { tabs: [], activeId: null }

/** Sections that live alongside sessions but never claim a tab of their own. */
const PAGE_SECTIONS: ReadonlyArray<string> = [
  "threads",
  "skills",
  "automations",
  "reviews",
  "instructions",
  "environments",
  "snapshots",
]

export function cloudTabId(threadId: string): string {
  return `cloud:${threadId}`
}

export function localTabId(sessionId: string): string {
  return `local:${sessionId}`
}

function isInternalAgentsPath(path: string): boolean {
  return path === HOME_PATH || path.startsWith("/agents/")
}

/** Drops anything that isn't a well-formed tab so a corrupt store can't wedge the app. */
export function sanitizeTabsState(raw: unknown): AgentTabsState {
  const source = raw as { tabs?: unknown; activeId?: unknown } | null
  if (!source || !Array.isArray(source.tabs)) return EMPTY_TABS_STATE
  const seen = new Set<string>()
  const tabs: Array<AgentTab> = []
  for (const entry of source.tabs) {
    const tab = entry as Partial<AgentTab> | null
    if (!tab || typeof tab !== "object") continue
    const { id, kind, path, title } = tab
    if (typeof id !== "string" || !id) continue
    if (typeof kind !== "string" || !KINDS.includes(kind as AgentTabKind)) {
      continue
    }
    if (typeof path !== "string" || !isInternalAgentsPath(path)) continue
    if (seen.has(id)) continue
    seen.add(id)
    tabs.push({
      id,
      kind: kind as AgentTabKind,
      path,
      title: typeof title === "string" && title ? title : "Session",
    })
    if (tabs.length >= MAX_TABS) break
  }
  const activeId =
    typeof source.activeId === "string" &&
    tabs.some((tab) => tab.id === source.activeId)
      ? source.activeId
      : null
  return { tabs, activeId }
}

/** Adds the tab (or refreshes an existing one) and makes it active. */
export function openTab(state: AgentTabsState, tab: AgentTab): AgentTabsState {
  const existing = state.tabs.find((item) => item.id === tab.id)
  if (existing) {
    return {
      tabs: state.tabs.map((item) =>
        item.id === tab.id ? { ...item, path: tab.path } : item
      ),
      activeId: tab.id,
    }
  }
  const active = state.tabs.find((item) => item.id === state.activeId)
  // A draft tab that starts a session becomes that session's tab, the way a
  // browser's new-tab page turns into the page you opened from it.
  if (
    active?.kind === "new" &&
    (tab.kind === "cloud" || tab.kind === "local")
  ) {
    return {
      tabs: state.tabs.map((item) => (item.id === active.id ? tab : item)),
      activeId: tab.id,
    }
  }
  return {
    tabs: [...state.tabs, tab].slice(-MAX_TABS),
    activeId: tab.id,
  }
}

export function activateTab(
  state: AgentTabsState,
  id: string | null
): AgentTabsState {
  if (id !== null && !state.tabs.some((tab) => tab.id === id)) return state
  if (state.activeId === id) return state
  return { ...state, activeId: id }
}

/** Removes a tab; when it was active the neighbour to its right takes over. */
export function closeTab(state: AgentTabsState, id: string): AgentTabsState {
  const index = state.tabs.findIndex((tab) => tab.id === id)
  if (index === -1) return state
  const tabs = state.tabs.filter((tab) => tab.id !== id)
  if (state.activeId !== id) return { tabs, activeId: state.activeId }
  const next = tabs[index] ?? tabs[index - 1] ?? null
  return { tabs, activeId: next?.id ?? null }
}

export function renameTab(
  state: AgentTabsState,
  id: string,
  title: string
): AgentTabsState {
  if (!title) return state
  const tab = state.tabs.find((item) => item.id === id)
  if (!tab || tab.title === title) return state
  return {
    ...state,
    tabs: state.tabs.map((item) =>
      item.id === id ? { ...item, title } : item
    ),
  }
}

/**
 * The tab a pathname belongs to, or `null` for the home screen. Titles here are
 * placeholders — thread pages rename their tab once the thread has loaded.
 */
export function tabForPathname(pathname: string): AgentTab | null {
  const path = pathname.replace(/\/+$/, "") || HOME_PATH
  if (path === HOME_PATH) return null
  if (path === NEW_TAB_PATH) {
    return { id: "new", kind: "new", path: NEW_TAB_PATH, title: "New session" }
  }
  const segments = path.split("/").filter(Boolean)
  if (segments[0] !== "agents" || !segments[1]) return null
  if (PAGE_SECTIONS.includes(segments[1])) return null
  if (segments[1] === "local") {
    const sessionId = segments[2]
    if (!sessionId) return null
    return {
      id: localTabId(sessionId),
      kind: "local",
      path: `/agents/local/${sessionId}`,
      title: "Local session",
    }
  }
  const threadId = segments[1]
  return {
    id: cloudTabId(threadId),
    kind: "cloud",
    // `/agents/<id>/plan` shares the thread's tab.
    path: `/agents/${threadId}`,
    title: "Session",
  }
}

let state: AgentTabsState = EMPTY_TABS_STATE
let hydrated = false
const listeners = new Set<() => void>()

function read(): AgentTabsState {
  if (typeof window === "undefined") return EMPTY_TABS_STATE
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    return raw ? sanitizeTabsState(JSON.parse(raw)) : EMPTY_TABS_STATE
  } catch {
    return EMPTY_TABS_STATE
  }
}

function persist(next: AgentTabsState): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // A full or blocked storage quota shouldn't break navigation.
  }
}

function emit(): void {
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function getSnapshot(): AgentTabsState {
  if (!hydrated && typeof window !== "undefined") {
    hydrated = true
    state = read()
  }
  return state
}

function getServerSnapshot(): AgentTabsState {
  return EMPTY_TABS_STATE
}

export function updateTabs(
  update: (current: AgentTabsState) => AgentTabsState
): AgentTabsState {
  const next = update(getSnapshot())
  if (next === state) return state
  state = next
  persist(next)
  emit()
  return next
}

/** Test-only: drops the in-memory store so cases don't leak into each other. */
export function resetTabsStoreForTests(): void {
  state = EMPTY_TABS_STATE
  hydrated = true
  emit()
}

export function useAgentTabs(): AgentTabsState {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}

/** Keeps a thread's tab labelled with the thread's own title. */
export function useSyncTabTitle(id: string, title: string | undefined): void {
  useEffect(() => {
    if (!title) return
    updateTabs((current) => renameTab(current, id, title))
  }, [id, title])
}
