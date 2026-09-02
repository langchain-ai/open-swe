import { useCallback, useEffect, useState } from "react"

import { DEFAULT_SIDEBAR_FILTERS } from "./sidebarFilter"
import type { SidebarFilters } from "./sidebarFilter"

export const SIDEBAR_PREFS_STORAGE_KEY = "open-swe.agents.sidebar-prefs"
const STORAGE_KEY = SIDEBAR_PREFS_STORAGE_KEY

const ORGANIZE_MODES = ["project", "list"] as const
const CHAT_SORTS = ["priority", "updated"] as const
const PINNED_SORTS = ["priority", "updated", "manual"] as const

export type OrganizeMode = (typeof ORGANIZE_MODES)[number]
export type ChatSort = (typeof CHAT_SORTS)[number]
export type PinnedSort = (typeof PINNED_SORTS)[number]

export interface SidebarPrefs {
  compact: boolean
  filters: SidebarFilters
  /**
   * Pins for local threads. Cloud pins live server-side (`thread_pins`); local
   * threads only exist on this machine, so their pins do too.
   */
  pinnedLocalIds: Array<string>
  /** Projects pinned into the sidebar's Pinned section. Client-side only. */
  pinnedProjectKeys: Array<string>
  collapsedProjectKeys: Array<string>
  expandedProjectKeys: Array<string>
  /** Which of "pinned" | "projects" | "recents" are collapsed. */
  collapsedSectionKeys: Array<string>
  organize: OrganizeMode
  sortChats: ChatSort
  sortPinned: PinnedSort
}

export const DEFAULT_SIDEBAR_PREFS: SidebarPrefs = {
  compact: false,
  filters: DEFAULT_SIDEBAR_FILTERS,
  pinnedLocalIds: [],
  pinnedProjectKeys: [],
  collapsedProjectKeys: [],
  expandedProjectKeys: [],
  collapsedSectionKeys: [],
  organize: "project",
  sortChats: "priority",
  sortPinned: "manual",
}

function asStringArray(value: unknown): Array<string> {
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === "string")
    : []
}

/** localStorage is untrusted: anything not in the allow-list falls back. */
function asEnum<T extends string>(
  value: unknown,
  allowed: ReadonlyArray<T>,
  fallback: T
): T {
  return typeof value === "string" &&
    (allowed as ReadonlyArray<string>).includes(value)
    ? (value as T)
    : fallback
}

function sanitizeFilters(value: unknown): SidebarFilters {
  const raw =
    value && typeof value === "object" ? (value as Record<string, unknown>) : {}
  return {
    sources: asStringArray(raw.sources) as SidebarFilters["sources"],
    includeAutomations:
      typeof raw.includeAutomations === "boolean"
        ? raw.includeAutomations
        : DEFAULT_SIDEBAR_FILTERS.includeAutomations,
    includeResolved:
      typeof raw.includeResolved === "boolean"
        ? raw.includeResolved
        : DEFAULT_SIDEBAR_FILTERS.includeResolved,
  }
}

function sanitizePrefs(value: unknown): SidebarPrefs {
  const raw =
    value && typeof value === "object" ? (value as Record<string, unknown>) : {}
  return {
    compact:
      typeof raw.compact === "boolean"
        ? raw.compact
        : DEFAULT_SIDEBAR_PREFS.compact,
    filters: sanitizeFilters(raw.filters),
    pinnedLocalIds: asStringArray(raw.pinnedLocalIds),
    pinnedProjectKeys: asStringArray(raw.pinnedProjectKeys),
    collapsedProjectKeys: asStringArray(raw.collapsedProjectKeys),
    expandedProjectKeys: asStringArray(raw.expandedProjectKeys),
    collapsedSectionKeys: asStringArray(raw.collapsedSectionKeys),
    organize: asEnum(
      raw.organize,
      ORGANIZE_MODES,
      DEFAULT_SIDEBAR_PREFS.organize
    ),
    sortChats: asEnum(
      raw.sortChats,
      CHAT_SORTS,
      DEFAULT_SIDEBAR_PREFS.sortChats
    ),
    sortPinned: asEnum(
      raw.sortPinned,
      PINNED_SORTS,
      DEFAULT_SIDEBAR_PREFS.sortPinned
    ),
  }
}

function toggleMembership(values: Array<string>, value: string): Array<string> {
  return values.includes(value)
    ? values.filter((entry) => entry !== value)
    : [...values, value]
}

function loadPrefs(): SidebarPrefs {
  if (typeof window === "undefined") return DEFAULT_SIDEBAR_PREFS
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_SIDEBAR_PREFS
    return sanitizePrefs(JSON.parse(raw))
  } catch {
    return DEFAULT_SIDEBAR_PREFS
  }
}

export function useSidebarPrefs() {
  const [prefs, setPrefs] = useState<SidebarPrefs>(loadPrefs)

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs))
    } catch {
      /* ignore persistence failures (private mode, quota, SSR) */
    }
  }, [prefs])

  const setCompact = useCallback(
    (compact: boolean) => setPrefs((prev) => ({ ...prev, compact })),
    []
  )
  const setFilters = useCallback(
    (filters: SidebarFilters) => setPrefs((prev) => ({ ...prev, filters })),
    []
  )
  const toggleLocalPin = useCallback(
    (threadId: string) =>
      setPrefs((prev) => ({
        ...prev,
        pinnedLocalIds: toggleMembership(prev.pinnedLocalIds, threadId),
      })),
    []
  )
  const toggleProjectPin = useCallback(
    (key: string) =>
      setPrefs((prev) => ({
        ...prev,
        pinnedProjectKeys: toggleMembership(prev.pinnedProjectKeys, key),
      })),
    []
  )
  const toggleProjectCollapsed = useCallback(
    (key: string) =>
      setPrefs((prev) => ({
        ...prev,
        collapsedProjectKeys: toggleMembership(prev.collapsedProjectKeys, key),
      })),
    []
  )
  const expandProject = useCallback(
    (key: string) =>
      setPrefs((prev) =>
        prev.expandedProjectKeys.includes(key)
          ? prev
          : { ...prev, expandedProjectKeys: [...prev.expandedProjectKeys, key] }
      ),
    []
  )

  const toggleSectionCollapsed = useCallback(
    (key: string) =>
      setPrefs((prev) => ({
        ...prev,
        collapsedSectionKeys: toggleMembership(prev.collapsedSectionKeys, key),
      })),
    []
  )
  const setView = useCallback(
    (
      patch: Partial<
        Pick<SidebarPrefs, "organize" | "sortChats" | "sortPinned">
      >
    ) => setPrefs((prev) => ({ ...prev, ...patch })),
    []
  )

  return {
    prefs,
    setCompact,
    setFilters,
    toggleLocalPin,
    toggleProjectPin,
    toggleProjectCollapsed,
    toggleSectionCollapsed,
    expandProject,
    setView,
  }
}
