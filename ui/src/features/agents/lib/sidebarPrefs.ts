import { useCallback, useEffect, useState } from "react"

import { DEFAULT_SIDEBAR_FILTERS } from "./sidebarFilter"
import type { SidebarFilters } from "./sidebarFilter"

export const SIDEBAR_PREFS_STORAGE_KEY = "open-swe.agents.sidebar-prefs"
const STORAGE_KEY = SIDEBAR_PREFS_STORAGE_KEY

export interface SidebarPrefs {
  compact: boolean
  filters: SidebarFilters
}

export const DEFAULT_SIDEBAR_PREFS: SidebarPrefs = {
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
  return {
    statuses: asStringArray(raw.statuses) as SidebarFilters["statuses"],
    sources: asStringArray(raw.sources) as SidebarFilters["sources"],
    locations: asStringArray(raw.locations) as SidebarFilters["locations"],
    pr: asStringArray(raw.pr) as SidebarFilters["pr"],
    models: asStringArray(raw.models),
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

  return {
    prefs,
    setCompact,
    setFilters,
    resetFilters,
  }
}

export type UseSidebarPrefs = ReturnType<typeof useSidebarPrefs>
