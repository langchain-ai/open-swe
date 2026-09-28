/**
 * Recently visited URLs, shown on a new browser tab. Entries are grouped by a
 * caller-chosen scope (the repository for cloud threads, the checkout for
 * local sessions) so a fresh thread on the same project offers the same dev
 * server links.
 */
import { create } from "zustand"
import { createJSONStorage, persist } from "zustand/middleware"
import { useShallow } from "zustand/react/shallow"

import {
  isLoopbackHost,
  normalizeBrowserUrl,
} from "@/features/agents/browser/browserUrl"

export interface BrowserHistoryEntry {
  readonly url: string
  readonly lastVisitedAt: number
  readonly title?: string
}

export const BROWSER_HISTORY_MAX_ENTRIES_PER_SCOPE = 50
export const BROWSER_HISTORY_MAX_SCOPES = 20
const BROWSER_HISTORY_MAX_URL_LENGTH = 2048
const BROWSER_HISTORY_MAX_TITLE_LENGTH = 512
const MAX_VALID_DATE_MS = 8_640_000_000_000_000
const BROWSER_HISTORY_STORAGE_KEY = "open-swe:browser-history"

export function isValidHistoryTimestamp(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value > 0 &&
    value <= MAX_VALID_DATE_MS
  )
}

/** Canonical stored form: normalised, credentials stripped, bounded length. */
export function normalizeHistoryUrl(raw: string): string | null {
  let parsed: URL
  try {
    parsed = new URL(normalizeBrowserUrl(raw))
  } catch {
    return null
  }
  parsed.username = ""
  parsed.password = ""
  return parsed.href.length > BROWSER_HISTORY_MAX_URL_LENGTH
    ? null
    : parsed.href
}

/**
 * Two loopback spellings of the same dev server are one history entry, and a
 * trailing slash does not make a page new.
 */
function lookupKey(normalized: string): string {
  const parsed = new URL(normalized)
  if (isLoopbackHost(parsed.hostname)) parsed.hostname = "localhost"
  if (parsed.pathname !== "/" && parsed.pathname.endsWith("/")) {
    parsed.pathname = parsed.pathname.slice(0, -1)
  }
  return parsed.href
}

export function upsertHistoryEntry(
  entries: ReadonlyArray<BrowserHistoryEntry>,
  url: string,
  at: number
): Array<BrowserHistoryEntry> {
  const key = lookupKey(url)
  const existing = entries.find((entry) => lookupKey(entry.url) === key)
  const rest = entries.filter((entry) => lookupKey(entry.url) !== key)
  const entry: BrowserHistoryEntry = existing
    ? { ...existing, url, lastVisitedAt: Math.max(at, existing.lastVisitedAt) }
    : { url, lastVisitedAt: at }
  return [entry, ...rest]
    .toSorted((left, right) => right.lastVisitedAt - left.lastVisitedAt)
    .slice(0, BROWSER_HISTORY_MAX_ENTRIES_PER_SCOPE)
}

function evictExcessScopes(
  byScope: Record<string, Array<BrowserHistoryEntry>>
): Record<string, Array<BrowserHistoryEntry>> {
  const scopes = Object.keys(byScope)
  if (scopes.length <= BROWSER_HISTORY_MAX_SCOPES) return byScope
  const kept = scopes
    .toSorted(
      (left, right) =>
        (byScope[right]?.[0]?.lastVisitedAt ?? 0) -
        (byScope[left]?.[0]?.lastVisitedAt ?? 0)
    )
    .slice(0, BROWSER_HISTORY_MAX_SCOPES)
  return Object.fromEntries(kept.map((scope) => [scope, byScope[scope] ?? []]))
}

export function migratePersistedBrowserHistory(persistedState: unknown): {
  byScope: Record<string, Array<BrowserHistoryEntry>>
} {
  if (!persistedState || typeof persistedState !== "object")
    return { byScope: {} }
  const raw = (persistedState as { byScope?: unknown }).byScope
  if (!raw || typeof raw !== "object" || Array.isArray(raw))
    return { byScope: {} }
  const byScope: Record<string, Array<BrowserHistoryEntry>> = {}
  for (const [scope, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!Array.isArray(value) || scope.length === 0) continue
    const seen = new Set<string>()
    const entries = value
      .flatMap<BrowserHistoryEntry>((candidate) => {
        if (!candidate || typeof candidate !== "object") return []
        const { url, lastVisitedAt, title } = candidate as Record<
          string,
          unknown
        >
        if (typeof url !== "string") return []
        const normalized = normalizeHistoryUrl(url)
        if (!normalized || !isValidHistoryTimestamp(lastVisitedAt)) return []
        return [
          {
            url: normalized,
            lastVisitedAt,
            ...(typeof title === "string" && title.length > 0
              ? { title: title.slice(0, BROWSER_HISTORY_MAX_TITLE_LENGTH) }
              : {}),
          },
        ]
      })
      .toSorted((left, right) => right.lastVisitedAt - left.lastVisitedAt)
      .filter((entry) => {
        const key = lookupKey(entry.url)
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
      .slice(0, BROWSER_HISTORY_MAX_ENTRIES_PER_SCOPE)
    if (entries.length > 0) byScope[scope] = entries
  }
  return { byScope: evictExcessScopes(byScope) }
}

interface BrowserHistoryStoreState {
  byScope: Record<string, Array<BrowserHistoryEntry>>
  recordVisit: (scope: string, url: string, at?: number) => void
  setTitle: (scope: string, url: string, title: string) => void
  removeUrl: (scope: string, url: string) => void
}

const memoryStorage = (): Storage => {
  const map = new Map<string, string>()
  return {
    get length() {
      return map.size
    },
    clear: () => map.clear(),
    getItem: (key) => map.get(key) ?? null,
    key: (index) => [...map.keys()][index] ?? null,
    removeItem: (key) => void map.delete(key),
    setItem: (key, value) => void map.set(key, value),
  }
}

export const useBrowserHistoryStore = create<BrowserHistoryStoreState>()(
  persist(
    (set, get) => ({
      byScope: {},
      recordVisit: (scope, url, at = Date.now()) => {
        const normalized = normalizeHistoryUrl(url)
        if (!normalized || scope.length === 0) return
        set((state) => ({
          byScope: evictExcessScopes({
            ...state.byScope,
            [scope]: upsertHistoryEntry(
              state.byScope[scope] ?? [],
              normalized,
              at
            ),
          }),
        }))
      },
      setTitle: (scope, url, title) => {
        const normalized = normalizeHistoryUrl(url)
        const entries = get().byScope[scope]
        const trimmed = title.trim().slice(0, BROWSER_HISTORY_MAX_TITLE_LENGTH)
        if (!normalized || !entries || trimmed.length === 0) return
        const key = lookupKey(normalized)
        const index = entries.findIndex((entry) => lookupKey(entry.url) === key)
        if (index === -1 || entries[index]?.title === trimmed) return
        set((state) => ({
          byScope: {
            ...state.byScope,
            [scope]: entries.map((entry, entryIndex) =>
              entryIndex === index ? { ...entry, title: trimmed } : entry
            ),
          },
        }))
      },
      removeUrl: (scope, url) => {
        const normalized = normalizeHistoryUrl(url)
        const entries = get().byScope[scope]
        if (!normalized || !entries) return
        const key = lookupKey(normalized)
        const next = entries.filter((entry) => lookupKey(entry.url) !== key)
        if (next.length === entries.length) return
        set((state) => {
          if (next.length === 0) {
            const { [scope]: _removed, ...rest } = state.byScope
            return { byScope: rest }
          }
          return { byScope: { ...state.byScope, [scope]: next } }
        })
      },
    }),
    {
      name: BROWSER_HISTORY_STORAGE_KEY,
      version: 1,
      storage: createJSONStorage(() =>
        typeof window === "undefined" ? memoryStorage() : window.localStorage
      ),
      partialize: (state) => ({ byScope: state.byScope }),
      migrate: migratePersistedBrowserHistory,
      merge: (persistedState, currentState) => ({
        ...currentState,
        ...migratePersistedBrowserHistory(persistedState),
      }),
    }
  )
)

const EMPTY_HISTORY: ReadonlyArray<BrowserHistoryEntry> = Object.freeze([])

export function useRecentBrowserHistory(
  scope: string,
  limit: number
): ReadonlyArray<BrowserHistoryEntry> {
  return useBrowserHistoryStore(
    useShallow((state) => {
      const entries = state.byScope[scope]
      return entries && entries.length > 0
        ? entries.slice(0, limit)
        : EMPTY_HISTORY
    })
  )
}
