import { useCallback, useEffect, useMemo, useState } from "react"

export type RightPanelSurface =
  | { id: "diff"; kind: "diff" }
  | { id: "files"; kind: "files" }
  | {
      id: `file:${string}`
      kind: "file"
      relativePath: string
      revealRequestId: number
    }
  | {
      id: `browser:${string}`
      kind: "preview"
      resourceId: string
    }
  | {
      id: `terminal:${string}`
      kind: "terminal"
      resourceId: string
    }
  | { id: "pull-request"; kind: "pull-request" }

export interface RightPanelState {
  isOpen: boolean
  activeSurfaceId: string | null
  surfaces: Array<RightPanelSurface>
}

const STORAGE_PREFIX = "open-swe.right-panel.v2:"
const EMPTY_STATE: RightPanelState = {
  isOpen: false,
  activeSurfaceId: null,
  surfaces: [],
}

function isSurface(value: unknown): value is RightPanelSurface {
  if (!value || typeof value !== "object") return false
  const surface = value as Partial<RightPanelSurface>
  if (typeof surface.id !== "string" || typeof surface.kind !== "string")
    return false
  if (surface.kind === "diff") return surface.id === "diff"
  if (surface.kind === "files") return surface.id === "files"
  if (surface.kind === "pull-request") return surface.id === "pull-request"
  if (surface.kind === "file") {
    return (
      surface.id.startsWith("file:") &&
      typeof surface.relativePath === "string" &&
      typeof surface.revealRequestId === "number"
    )
  }
  if (surface.kind === "preview") {
    return (
      surface.id.startsWith("browser:") &&
      typeof surface.resourceId === "string"
    )
  }
  if (surface.kind === "terminal") {
    return (
      surface.id.startsWith("terminal:") &&
      typeof surface.resourceId === "string"
    )
  }
  return false
}

export function normalizeRightPanelState(value: unknown): RightPanelState {
  if (!value || typeof value !== "object") return EMPTY_STATE
  const candidate = value as Partial<RightPanelState>
  const surfaces = Array.isArray(candidate.surfaces)
    ? candidate.surfaces.filter(isSurface)
    : []
  const activeSurfaceId = surfaces.some(
    (surface) => surface.id === candidate.activeSurfaceId
  )
    ? (candidate.activeSurfaceId ?? null)
    : (surfaces[0]?.id ?? null)
  return {
    surfaces,
    activeSurfaceId,
    isOpen: candidate.isOpen === true && surfaces.length > 0,
  }
}

export function upsertRightPanelSurface(
  state: RightPanelState,
  surface: RightPanelSurface
): RightPanelState {
  const existing = state.surfaces.find((entry) => entry.id === surface.id)
  return {
    isOpen: true,
    activeSurfaceId: surface.id,
    surfaces: existing
      ? state.surfaces.map((entry) =>
          entry.id === surface.id ? surface : entry
        )
      : [...state.surfaces, surface],
  }
}

export function closeRightPanelSurface(
  state: RightPanelState,
  surfaceId: string
): RightPanelState {
  const index = state.surfaces.findIndex((surface) => surface.id === surfaceId)
  if (index < 0) return state
  const surfaces = state.surfaces.filter((surface) => surface.id !== surfaceId)
  const fallback = surfaces[Math.min(index, surfaces.length - 1)] ?? null
  return {
    isOpen: surfaces.length > 0 && state.isOpen,
    surfaces,
    activeSurfaceId:
      state.activeSurfaceId === surfaceId
        ? (fallback?.id ?? null)
        : state.activeSurfaceId,
  }
}

function readState(scope: string): RightPanelState {
  if (typeof window === "undefined") return EMPTY_STATE
  try {
    const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${scope}`)
    return raw ? normalizeRightPanelState(JSON.parse(raw)) : EMPTY_STATE
  } catch {
    return EMPTY_STATE
  }
}

function writeState(scope: string, state: RightPanelState) {
  if (typeof window === "undefined") return
  window.localStorage.setItem(
    `${STORAGE_PREFIX}${scope}`,
    JSON.stringify(state)
  )
}

function browserSurface(): Extract<RightPanelSurface, { kind: "preview" }> {
  const id =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return { id: `browser:${id}`, kind: "preview", resourceId: id }
}

export function useRightPanelStore(scope: string) {
  const [state, setState] = useState<RightPanelState>(() => readState(scope))
  useEffect(() => setState(readState(scope)), [scope])
  const update = useCallback(
    (updater: (current: RightPanelState) => RightPanelState) => {
      setState((current) => {
        const next = updater(current)
        writeState(scope, next)
        return next
      })
    },
    [scope]
  )
  const open = useCallback(
    (surface: RightPanelSurface) =>
      update((current) => upsertRightPanelSurface(current, surface)),
    [update]
  )
  const openDiff = useCallback(() => open({ id: "diff", kind: "diff" }), [open])
  const openFiles = useCallback(
    () => open({ id: "files", kind: "files" }),
    [open]
  )
  const openPullRequest = useCallback(
    () => open({ id: "pull-request", kind: "pull-request" }),
    [open]
  )
  const openBrowser = useCallback(() => open(browserSurface()), [open])
  const openTerminal = useCallback(
    (resourceId: string) =>
      open({
        id: `terminal:${resourceId}`,
        kind: "terminal",
        resourceId,
      }),
    [open]
  )
  const openFile = useCallback(
    (relativePath: string) =>
      update((current) => {
        const withoutFiles = {
          ...current,
          surfaces: current.surfaces.filter(
            (surface) => surface.kind !== "files"
          ),
        }
        const id = `file:${relativePath}` as const
        const existing = withoutFiles.surfaces.find(
          (surface) => surface.id === id && surface.kind === "file"
        )
        return upsertRightPanelSurface(withoutFiles, {
          id,
          kind: "file",
          relativePath,
          revealRequestId:
            existing?.kind === "file" ? existing.revealRequestId + 1 : 1,
        })
      }),
    [update]
  )
  const activate = useCallback(
    (surfaceId: string) =>
      update((current) =>
        current.surfaces.some((surface) => surface.id === surfaceId)
          ? { ...current, isOpen: true, activeSurfaceId: surfaceId }
          : current
      ),
    [update]
  )
  const closeSurface = useCallback(
    (surfaceId: string) =>
      update((current) => closeRightPanelSurface(current, surfaceId)),
    [update]
  )
  const closeOthers = useCallback(
    (surfaceId: string) =>
      update((current) => {
        const surface = current.surfaces.find((entry) => entry.id === surfaceId)
        return surface
          ? { isOpen: true, activeSurfaceId: surfaceId, surfaces: [surface] }
          : current
      }),
    [update]
  )
  const closeToRight = useCallback(
    (surfaceId: string) =>
      update((current) => {
        const index = current.surfaces.findIndex(
          (surface) => surface.id === surfaceId
        )
        return index < 0
          ? current
          : {
              ...current,
              surfaces: current.surfaces.slice(0, index + 1),
              activeSurfaceId: surfaceId,
            }
      }),
    [update]
  )
  const closeAll = useCallback(() => update(() => EMPTY_STATE), [update])
  const setOpen = useCallback(
    (isOpen: boolean) =>
      update((current) => ({
        ...current,
        isOpen: isOpen && current.surfaces.length > 0,
      })),
    [update]
  )
  const reconcileTerminals = useCallback(
    (resourceIds: ReadonlyArray<string>) =>
      update((current) => {
        const live = new Set(resourceIds)
        let next = current
        for (const surface of current.surfaces) {
          if (surface.kind === "terminal" && !live.has(surface.resourceId)) {
            next = closeRightPanelSurface(next, surface.id)
          }
        }
        return next
      }),
    [update]
  )
  return useMemo(
    () => ({
      ...state,
      openDiff,
      openFiles,
      openPullRequest,
      openBrowser,
      openTerminal,
      openFile,
      activate,
      closeSurface,
      closeOthers,
      closeToRight,
      closeAll,
      setOpen,
      reconcileTerminals,
    }),
    [
      activate,
      closeAll,
      closeOthers,
      closeSurface,
      closeToRight,
      openBrowser,
      openDiff,
      openFile,
      openFiles,
      openPullRequest,
      openTerminal,
      reconcileTerminals,
      setOpen,
      state,
    ]
  )
}

export type RightPanelController = ReturnType<typeof useRightPanelStore>
