/**
 * Thread-scoped diff selection.
 *
 * The panel never stores diff data — only which source the Changes surface is
 * pointed at: the workspace's working tree, or everything the branch changes
 * against its base. Diffs are fetched live per scope, so a thread whose sandbox
 * is gone can still show its branch changes instead of an empty result.
 */
import { create } from "zustand"
import { createJSONStorage, persist } from "zustand/middleware"

import {
  scopedThreadKey,
  type PanelThreadRef,
} from "@/features/agents/lib/rightPanelStore"

export const DIFF_SCOPE_KINDS = ["working-tree", "branch"] as const
export type DiffScopeKind = (typeof DIFF_SCOPE_KINDS)[number]

export type DiffPanelSelection = { kind: DiffScopeKind }

const WORKING_TREE_SELECTION: DiffPanelSelection = { kind: "working-tree" }
const BRANCH_SELECTION: DiffPanelSelection = { kind: "branch" }

const DIFF_PANEL_STORAGE_KEY = "open-swe:diff-panel-state"
const DIFF_PANEL_STORAGE_VERSION = 2

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
    // "thread"/"pull-request" are the v1 names for the same two sources.
    if (kind === "working-tree" || kind === "thread")
      byThreadKey[threadKey] = WORKING_TREE_SELECTION
    else if (kind === "branch" || kind === "pull-request")
      byThreadKey[threadKey] = BRANCH_SELECTION
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
                kind === "branch" ? BRANCH_SELECTION : WORKING_TREE_SELECTION,
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
 * thread that already has a pull request defaults to its branch changes: that
 * diff is served from GitHub, so it survives the sandbox the working tree
 * depends on. A branch that was never pushed is selectable but not the
 * default, since GitHub has nothing to compare yet.
 */
export function selectThreadDiffScope(
  byThreadKey: Record<string, DiffPanelSelection>,
  ref: PanelThreadRef | null | undefined,
  branchAvailable = false,
  branchIsDefault = branchAvailable
): DiffScopeKind {
  const stored = ref ? byThreadKey[scopedThreadKey(ref)] : undefined
  if (stored?.kind === "branch")
    return branchAvailable ? stored.kind : "working-tree"
  if (stored?.kind === "working-tree") return stored.kind
  return branchAvailable && branchIsDefault ? "branch" : "working-tree"
}
