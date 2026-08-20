/**
 * Thread-scoped diff selection.
 *
 * The panel never stores diff data — only which source the Changes surface is
 * pointed at. Diffs are fetched live per scope, so a thread whose sandbox is
 * gone can still show its pull request's diff instead of an empty result.
 */
import { create } from "zustand"
import { createJSONStorage, persist } from "zustand/middleware"

import {
  scopedThreadKey,
  type PanelThreadRef,
} from "@/features/agents/lib/rightPanelStore"

export const DIFF_SCOPE_KINDS = ["thread", "pull-request"] as const
export type DiffScopeKind = (typeof DIFF_SCOPE_KINDS)[number]

export type DiffPanelSelection = { kind: DiffScopeKind }

const THREAD_SELECTION: DiffPanelSelection = { kind: "thread" }
const PULL_REQUEST_SELECTION: DiffPanelSelection = { kind: "pull-request" }

const DIFF_PANEL_STORAGE_KEY = "open-swe:diff-panel-state"
const DIFF_PANEL_STORAGE_VERSION = 1

interface DiffPanelStoreState {
  byThreadKey: Record<string, DiffPanelSelection>
  selectScope: (ref: PanelThreadRef, kind: DiffScopeKind) => void
  removeThread: (ref: PanelThreadRef) => void
}

/**
 * Persisted selections are untrusted input: every entry is re-validated and
 * anything unrecognized is dropped rather than trusted.
 */
export function migratePersistedDiffPanelState(persistedState: unknown): {
  byThreadKey: Record<string, DiffPanelSelection>
} {
  if (!persistedState || typeof persistedState !== "object")
    return { byThreadKey: {} }
  if (!("byThreadKey" in persistedState)) return { byThreadKey: {} }
  const raw = (persistedState as { byThreadKey: unknown }).byThreadKey
  if (!raw || typeof raw !== "object") return { byThreadKey: {} }

  const byThreadKey: Record<string, DiffPanelSelection> = {}
  for (const [threadKey, value] of Object.entries(
    raw as Record<string, unknown>
  )) {
    if (!threadKey || !value || typeof value !== "object") continue
    const kind = (value as { kind?: unknown }).kind
    if (kind === "thread") byThreadKey[threadKey] = THREAD_SELECTION
    else if (kind === "pull-request")
      byThreadKey[threadKey] = PULL_REQUEST_SELECTION
  }
  return { byThreadKey }
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

export const useDiffPanelStore = create<DiffPanelStoreState>()(
  persist(
    (set) => ({
      byThreadKey: {},
      selectScope: (ref, kind) =>
        set((state) => {
          const threadKey = scopedThreadKey(ref)
          if (state.byThreadKey[threadKey]?.kind === kind) return state
          return {
            byThreadKey: {
              ...state.byThreadKey,
              [threadKey]:
                kind === "pull-request"
                  ? PULL_REQUEST_SELECTION
                  : THREAD_SELECTION,
            },
          }
        }),
      removeThread: (ref) =>
        set((state) => {
          const threadKey = scopedThreadKey(ref)
          if (!(threadKey in state.byThreadKey)) return state
          const { [threadKey]: _removed, ...byThreadKey } = state.byThreadKey
          return { byThreadKey }
        }),
    }),
    {
      name: DIFF_PANEL_STORAGE_KEY,
      version: DIFF_PANEL_STORAGE_VERSION,
      storage: createJSONStorage(() =>
        typeof window === "undefined" ? memoryStorage() : window.localStorage
      ),
      partialize: (state) => ({ byThreadKey: state.byThreadKey }),
      migrate: migratePersistedDiffPanelState,
    }
  )
)

/**
 * The scope the Changes surface should read. Absent an explicit choice a
 * thread with a pull request defaults to it: the PR diff is served from
 * GitHub, so it survives the sandbox the thread's own diff depends on.
 */
export function selectThreadDiffScope(
  byThreadKey: Record<string, DiffPanelSelection>,
  ref: PanelThreadRef | null | undefined,
  hasPullRequest = false
): DiffScopeKind {
  const stored = ref ? byThreadKey[scopedThreadKey(ref)] : undefined
  if (stored?.kind === "pull-request")
    return hasPullRequest ? stored.kind : "thread"
  if (stored?.kind === "thread") return stored.kind
  return hasPullRequest ? "pull-request" : "thread"
}
