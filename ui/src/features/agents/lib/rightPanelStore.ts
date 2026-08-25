/**
 * Thread-scoped right-panel surface state.
 *
 * This is intentionally a shallow workspace model: it owns an ordered set of
 * surface descriptors and the active surface, while each feature continues to
 * own its durable resource state. Terminal surfaces point at terminal group
 * ids, file surfaces point at workspace paths, and diff/files/agents remain
 * singleton surfaces.
 */
import { create } from "zustand"
import { createJSONStorage, persist } from "zustand/middleware"

/** A thread the panel can be scoped to: a cloud agent thread or a local session. */
export interface PanelThreadRef {
  scope: "cloud" | "local"
  threadId: string
}

export function scopedThreadKey(ref: PanelThreadRef): string {
  return `${ref.scope}:${ref.threadId}`
}

export const RIGHT_PANEL_KINDS = [
  "diff",
  "files",
  "file",
  "preview",
  "terminal",
  "agents",
] as const
export type RightPanelKind = (typeof RIGHT_PANEL_KINDS)[number]

export type RightPanelSurface =
  | { id: `browser:${string}`; kind: "preview"; resourceId: string }
  | { id: "browser:new"; kind: "preview"; resourceId: null }
  | {
      id: `terminal:${string}`
      kind: "terminal"
      resourceId: string
      terminalIds: Array<string>
      activeTerminalId: string
      splitDirection?: "horizontal" | "vertical"
    }
  | { id: "diff"; kind: "diff" }
  | { id: "files"; kind: "files" }
  | {
      id: `file:${string}`
      kind: "file"
      relativePath: string
      revealLine: number | null
      revealRequestId: number
    }
  | { id: "agents"; kind: "agents" }

const RIGHT_PANEL_STORAGE_KEY = "open-swe:right-panel-state"
const RIGHT_PANEL_STORAGE_VERSION = 2

export interface ThreadRightPanelState {
  isOpen: boolean
  activeSurfaceId: string | null
  surfaces: Array<RightPanelSurface>
}

type SingletonKind = Exclude<RightPanelKind, "file" | "preview" | "terminal">

interface RightPanelStoreState {
  byThreadKey: Record<string, ThreadRightPanelState>
  open: (
    ref: PanelThreadRef,
    kind: Exclude<RightPanelKind, "file" | "terminal">
  ) => void
  openBrowser: (ref: PanelThreadRef, tabId: string | null) => void
  openFile: (ref: PanelThreadRef, relativePath: string, line?: number) => void
  openTerminal: (ref: PanelThreadRef, terminalId: string) => void
  activateSurface: (ref: PanelThreadRef, surfaceId: string) => void
  closeSurface: (ref: PanelThreadRef, surfaceId: string) => void
  closeOtherSurfaces: (ref: PanelThreadRef, surfaceId: string) => void
  closeSurfacesToRight: (ref: PanelThreadRef, surfaceId: string) => void
  closeAllSurfaces: (ref: PanelThreadRef) => void
  reconcileTerminalSurfaces: (
    ref: PanelThreadRef,
    terminalIds: ReadonlyArray<string>
  ) => void
  show: (ref: PanelThreadRef) => void
  close: (ref: PanelThreadRef) => void
  toggleVisibility: (ref: PanelThreadRef) => void
  toggle: (
    ref: PanelThreadRef,
    kind: Exclude<RightPanelKind, "file" | "terminal">
  ) => void
  removeThread: (ref: PanelThreadRef) => void
}

const EMPTY_THREAD_STATE: ThreadRightPanelState = {
  isOpen: false,
  activeSurfaceId: null,
  surfaces: [],
}

const singletonSurface = (kind: SingletonKind): RightPanelSurface => {
  switch (kind) {
    case "diff":
      return { id: "diff", kind }
    case "files":
      return { id: "files", kind }
    case "agents":
      return { id: "agents", kind }
  }
}

const browserSurface = (tabId: string | null): RightPanelSurface =>
  tabId
    ? { id: `browser:${tabId}`, kind: "preview", resourceId: tabId }
    : { id: "browser:new", kind: "preview", resourceId: null }

const fileSurface = (
  relativePath: string,
  revealLine: number | null,
  revealRequestId: number
): RightPanelSurface => ({
  id: `file:${relativePath}`,
  kind: "file",
  relativePath,
  revealLine,
  revealRequestId,
})

const terminalSurface = (terminalId: string): RightPanelSurface => ({
  id: `terminal:${terminalId}`,
  kind: "terminal",
  resourceId: terminalId,
  terminalIds: [terminalId],
  activeTerminalId: terminalId,
})

const upsertSurface = (
  current: ThreadRightPanelState,
  surface: RightPanelSurface,
  activate = true
): ThreadRightPanelState => ({
  isOpen: true,
  surfaces: current.surfaces.some((entry) => entry.id === surface.id)
    ? current.surfaces
    : [...current.surfaces, surface],
  activeSurfaceId: activate ? surface.id : current.activeSurfaceId,
})

const updateThread = (
  byThreadKey: Record<string, ThreadRightPanelState>,
  threadKey: string,
  updater: (current: ThreadRightPanelState) => ThreadRightPanelState
): Record<string, ThreadRightPanelState> => {
  const current = byThreadKey[threadKey] ?? EMPTY_THREAD_STATE
  const next = updater(current)
  if (
    !next.isOpen &&
    next.activeSurfaceId === null &&
    next.surfaces.length === 0
  ) {
    if (!(threadKey in byThreadKey)) return byThreadKey
    const { [threadKey]: _removed, ...rest } = byThreadKey
    return rest
  }
  if (next === current) return byThreadKey
  return { ...byThreadKey, [threadKey]: next }
}

function normalizeRevealLine(line: number | undefined): number | null {
  if (line === undefined || !Number.isFinite(line)) return null
  return Math.max(1, Math.trunc(line))
}

/**
 * Persisted panel state is untrusted input: every surface is re-validated
 * field by field and anything unrecognized is dropped rather than trusted.
 */
export function migratePersistedRightPanelState(persistedState: unknown): {
  byThreadKey: Record<string, ThreadRightPanelState>
} {
  if (!persistedState || typeof persistedState !== "object")
    return { byThreadKey: {} }
  if (!("byThreadKey" in persistedState)) return { byThreadKey: {} }
  const raw = persistedState.byThreadKey
  if (!raw || typeof raw !== "object") return { byThreadKey: {} }

  const byThreadKey = Object.fromEntries(
    Object.entries(raw as Record<string, unknown>).map(([threadKey, value]) => {
      const threadState =
        value && typeof value === "object"
          ? (value as Record<string, unknown>)
          : null
      const surfaces = Array.isArray(threadState?.surfaces)
        ? threadState.surfaces.flatMap<RightPanelSurface>((entry) => {
            const surface =
              entry && typeof entry === "object"
                ? (entry as Record<string, unknown>)
                : null
            if (!surface || typeof surface.id !== "string") return []
            const kind = surface.kind
            if (kind === "diff" || kind === "files" || kind === "agents") {
              return surface.id === kind ? [singletonSurface(kind)] : []
            }
            if (kind === "file") {
              if (
                typeof surface.relativePath !== "string" ||
                surface.relativePath.length === 0
              ) {
                return []
              }
              const revealLine =
                typeof surface.revealLine === "number" &&
                Number.isFinite(surface.revealLine)
                  ? Math.max(1, Math.trunc(surface.revealLine))
                  : null
              const revealRequestId =
                typeof surface.revealRequestId === "number" &&
                Number.isSafeInteger(surface.revealRequestId) &&
                surface.revealRequestId >= 0
                  ? surface.revealRequestId
                  : 0
              return [
                fileSurface(surface.relativePath, revealLine, revealRequestId),
              ]
            }
            if (kind === "preview") {
              if (surface.id === "browser:new") return [browserSurface(null)]
              return typeof surface.resourceId === "string" &&
                surface.id === `browser:${surface.resourceId}`
                ? [browserSurface(surface.resourceId)]
                : []
            }
            if (kind !== "terminal") return []
            if (
              typeof surface.resourceId !== "string" ||
              surface.id !== `terminal:${surface.resourceId}`
            ) {
              return []
            }
            const terminalIds = Array.isArray(surface.terminalIds)
              ? [
                  ...new Set(
                    surface.terminalIds.filter(
                      (terminalId): terminalId is string =>
                        typeof terminalId === "string"
                    )
                  ),
                ]
              : [surface.resourceId]
            const ids =
              terminalIds.length > 0 ? terminalIds : [surface.resourceId]
            const activeTerminalId =
              typeof surface.activeTerminalId === "string" &&
              ids.includes(surface.activeTerminalId)
                ? surface.activeTerminalId
                : ids[0]!
            return [
              {
                id: `terminal:${surface.resourceId}`,
                kind: "terminal",
                resourceId: surface.resourceId,
                terminalIds: ids,
                activeTerminalId,
                ...(surface.splitDirection === "vertical"
                  ? { splitDirection: "vertical" as const }
                  : {}),
              },
            ]
          })
        : []
      const rawActiveSurfaceId = threadState?.activeSurfaceId
      const persistedActiveSurfaceId = surfaces.some(
        (surface) => surface.id === rawActiveSurfaceId
      )
        ? (rawActiveSurfaceId as string)
        : null
      // A migration that dropped every surface must not reopen an empty panel.
      const isOpen =
        surfaces.length > 0 &&
        (typeof threadState?.isOpen === "boolean"
          ? threadState.isOpen
          : persistedActiveSurfaceId !== null)
      // An open panel needs an active surface: if migration dropped the
      // persisted one, fall back to the first survivor.
      const activeSurfaceId =
        persistedActiveSurfaceId ?? (isOpen ? (surfaces[0]?.id ?? null) : null)
      return [threadKey, { isOpen, surfaces, activeSurfaceId }]
    })
  )
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

export const useRightPanelStore = create<RightPanelStoreState>()(
  persist(
    (set) => ({
      byThreadKey: {},
      open: (ref, kind) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) => {
              if (kind === "preview") {
                const existing = current.surfaces.find(
                  (surface) => surface.kind === "preview"
                )
                return upsertSurface(current, existing ?? browserSurface(null))
              }
              return upsertSurface(current, singletonSurface(kind))
            }
          ),
        })),
      openBrowser: (ref, tabId) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) => {
              const surface = browserSurface(tabId)
              const withoutPlaceholder = tabId
                ? current.surfaces.filter((entry) => entry.id !== "browser:new")
                : current.surfaces
              return upsertSurface(
                { ...current, surfaces: withoutPlaceholder },
                surface
              )
            }
          ),
        })),
      openFile: (ref, relativePath, line) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) => {
              const withoutStandaloneExplorer = current.surfaces.filter(
                (surface) => surface.kind !== "files"
              )
              const surfaceId = `file:${relativePath}` as const
              const existing = withoutStandaloneExplorer.find(
                (
                  surface
                ): surface is Extract<RightPanelSurface, { kind: "file" }> =>
                  surface.id === surfaceId
              )
              const surface = fileSurface(
                relativePath,
                normalizeRevealLine(line),
                (existing?.revealRequestId ?? 0) + 1
              )
              return {
                isOpen: true,
                activeSurfaceId: surface.id,
                surfaces: existing
                  ? withoutStandaloneExplorer.map((entry) =>
                      entry.id === surface.id ? surface : entry
                    )
                  : [...withoutStandaloneExplorer, surface],
              }
            }
          ),
        })),
      openTerminal: (ref, terminalId) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) => upsertSurface(current, terminalSurface(terminalId))
          ),
        })),
      activateSurface: (ref, surfaceId) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) =>
              current.surfaces.some((surface) => surface.id === surfaceId)
                ? { ...current, isOpen: true, activeSurfaceId: surfaceId }
                : current
          ),
        })),
      closeSurface: (ref, surfaceId) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) => {
              const index = current.surfaces.findIndex(
                (surface) => surface.id === surfaceId
              )
              if (index < 0) return current
              const surfaces = current.surfaces.filter(
                (surface) => surface.id !== surfaceId
              )
              if (current.activeSurfaceId !== surfaceId) {
                return {
                  ...current,
                  isOpen: surfaces.length > 0 && current.isOpen,
                  surfaces,
                }
              }
              const fallback =
                surfaces[Math.min(index, surfaces.length - 1)] ?? null
              return {
                ...current,
                isOpen: surfaces.length > 0 && current.isOpen,
                surfaces,
                activeSurfaceId: fallback?.id ?? null,
              }
            }
          ),
        })),
      closeOtherSurfaces: (ref, surfaceId) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) => {
              const surface = current.surfaces.find(
                (entry) => entry.id === surfaceId
              )
              if (!surface || current.surfaces.length === 1) return current
              return {
                ...current,
                isOpen: true,
                surfaces: [surface],
                activeSurfaceId: surface.id,
              }
            }
          ),
        })),
      closeSurfacesToRight: (ref, surfaceId) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) => {
              const index = current.surfaces.findIndex(
                (surface) => surface.id === surfaceId
              )
              if (index < 0 || index === current.surfaces.length - 1)
                return current
              const surfaces = current.surfaces.slice(0, index + 1)
              const activeStillExists = surfaces.some(
                (surface) => surface.id === current.activeSurfaceId
              )
              return {
                ...current,
                surfaces,
                activeSurfaceId: activeStillExists
                  ? current.activeSurfaceId
                  : surfaceId,
              }
            }
          ),
        })),
      closeAllSurfaces: (ref) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) =>
              current.surfaces.length === 0
                ? current
                : {
                    ...current,
                    isOpen: false,
                    surfaces: [],
                    activeSurfaceId: null,
                  }
          ),
        })),
      reconcileTerminalSurfaces: (ref, terminalIds) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) => {
              const live = new Set(terminalIds)
              const surfaces = current.surfaces.filter(
                (surface) =>
                  surface.kind !== "terminal" || live.has(surface.resourceId)
              )
              if (surfaces.length === current.surfaces.length) return current
              const activeStillExists = surfaces.some(
                (surface) => surface.id === current.activeSurfaceId
              )
              return {
                ...current,
                isOpen: surfaces.length > 0 && current.isOpen,
                surfaces,
                activeSurfaceId: activeStillExists
                  ? current.activeSurfaceId
                  : (surfaces.at(-1)?.id ?? null),
              }
            }
          ),
        })),
      show: (ref) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) =>
              current.isOpen ? current : { ...current, isOpen: true }
          ),
        })),
      close: (ref) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) =>
              current.isOpen ? { ...current, isOpen: false } : current
          ),
        })),
      toggleVisibility: (ref) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) => ({
              ...current,
              isOpen: !current.isOpen,
            })
          ),
        })),
      toggle: (ref, kind) =>
        set((state) => ({
          byThreadKey: updateThread(
            state.byThreadKey,
            scopedThreadKey(ref),
            (current) => {
              const active = current.surfaces.find(
                (surface) => surface.id === current.activeSurfaceId
              )
              if (current.isOpen && active?.kind === kind)
                return { ...current, isOpen: false }
              if (kind === "preview") {
                const existing = current.surfaces.find(
                  (surface) => surface.kind === "preview"
                )
                return upsertSurface(current, existing ?? browserSurface(null))
              }
              return upsertSurface(current, singletonSurface(kind))
            }
          ),
        })),
      removeThread: (ref) =>
        set((state) => {
          const threadKey = scopedThreadKey(ref)
          if (!(threadKey in state.byThreadKey)) return state
          const { [threadKey]: _removed, ...rest } = state.byThreadKey
          return { byThreadKey: rest }
        }),
    }),
    {
      name: RIGHT_PANEL_STORAGE_KEY,
      version: RIGHT_PANEL_STORAGE_VERSION,
      storage: createJSONStorage(() =>
        typeof window === "undefined" ? memoryStorage() : window.localStorage
      ),
      partialize: (state) => ({ byThreadKey: state.byThreadKey }),
      migrate: migratePersistedRightPanelState,
    }
  )
)

export function selectThreadRightPanelState(
  byThreadKey: Record<string, ThreadRightPanelState>,
  ref: PanelThreadRef | null | undefined
): ThreadRightPanelState {
  if (!ref) return EMPTY_THREAD_STATE
  return byThreadKey[scopedThreadKey(ref)] ?? EMPTY_THREAD_STATE
}

/** The selected surface even while the panel is hidden, so a layout control can restore it. */
export function selectSelectedRightPanelSurface(
  byThreadKey: Record<string, ThreadRightPanelState>,
  ref: PanelThreadRef | null | undefined
): RightPanelSurface | null {
  const state = selectThreadRightPanelState(byThreadKey, ref)
  return (
    state.surfaces.find((surface) => surface.id === state.activeSurfaceId) ??
    null
  )
}

export function selectActiveRightPanelSurface(
  byThreadKey: Record<string, ThreadRightPanelState>,
  ref: PanelThreadRef | null | undefined
): RightPanelSurface | null {
  const state = selectThreadRightPanelState(byThreadKey, ref)
  if (!state.isOpen) return null
  return selectSelectedRightPanelSurface(byThreadKey, ref)
}
