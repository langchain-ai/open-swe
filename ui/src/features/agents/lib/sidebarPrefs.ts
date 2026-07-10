import { useCallback, useEffect, useState } from "react"

import { DEFAULT_SIDEBAR_FILTERS } from "./sidebarFilter"
import type { SidebarFilters, SidebarGroupMode } from "./sidebarFilter"

const STORAGE_KEY = "open-swe.agents.sidebar-prefs"

const GROUP_MODES: ReadonlyArray<SidebarGroupMode> = [
  "none",
  "date",
  "status",
  "repo",
]

export interface SidebarPrefs {
  group: SidebarGroupMode
  compact: boolean
  filters: SidebarFilters
}

export const DEFAULT_SIDEBAR_PREFS: SidebarPrefs = {
  group: "date",
  compact: false,
  filters: DEFAULT_SIDEBAR_FILTERS,
}

function asStringArray(value: unknown): Array<string> {
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === "string")
    : []
}

function sanitizeFilters(value: unknown): SidebarFilters {
  const raw =
    value && typeof value === "object" ? (value as Record<string, unknown>) : {}
  const ownership = raw.ownership
  return {
    ownership:
      ownership === "mine" || ownership === "shared" || ownership === "all"
        ? ownership
        : DEFAULT_SIDEBAR_FILTERS.ownership,
    statuses: asStringArray(raw.statuses) as SidebarFilters["statuses"],
    sources: asStringArray(raw.sources) as SidebarFilters["sources"],
    pr: asStringArray(raw.pr) as SidebarFilters["pr"],
    models: asStringArray(raw.models),
    repos: asStringArray(raw.repos),
    includeResolved:
      typeof raw.includeResolved === "boolean"
        ? raw.includeResolved
        : DEFAULT_SIDEBAR_FILTERS.includeResolved,
  }
}

function sanitizePrefs(value: unknown): SidebarPrefs {
  const raw =
    value && typeof value === "object" ? (value as Record<string, unknown>) : {}
  const group = raw.group
  return {
    group: GROUP_MODES.includes(group as SidebarGroupMode)
      ? (group as SidebarGroupMode)
      : DEFAULT_SIDEBAR_PREFS.group,
    compact:
      typeof raw.compact === "boolean"
        ? raw.compact
        : DEFAULT_SIDEBAR_PREFS.compact,
    filters: sanitizeFilters(raw.filters),
  }
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

  const setGroup = useCallback(
    (group: SidebarGroupMode) => setPrefs((prev) => ({ ...prev, group })),
    []
  )
  const setCompact = useCallback(
    (compact: boolean) => setPrefs((prev) => ({ ...prev, compact })),
    []
  )
  const setFilters = useCallback(
    (filters: SidebarFilters) => setPrefs((prev) => ({ ...prev, filters })),
    []
  )
  const resetFilters = useCallback(
    () =>
      setPrefs((prev) => ({
        ...prev,
        filters: { ...DEFAULT_SIDEBAR_FILTERS },
      })),
    []
  )

  return { prefs, setGroup, setCompact, setFilters, resetFilters }
}

export type UseSidebarPrefs = ReturnType<typeof useSidebarPrefs>
