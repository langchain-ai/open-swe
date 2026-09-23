import {
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useState } from "react"
import {
  api,
  type WorkspaceSettings,
  type WorkspaceSettingsOverrides,
  type WorkspaceSettingsView,
} from "@/lib/api"

/** Which settings record a page edits: the instance, or one workspace's overrides. */
export type SettingsScope =
  | { kind: "instance" }
  | { kind: "workspace"; slug: string }

export const INSTANCE_SCOPE: SettingsScope = { kind: "instance" }

export function settingsQueryKey(scope: SettingsScope): Array<string> {
  return scope.kind === "instance"
    ? ["instanceSettings"]
    : ["workspaceSettings", scope.slug]
}

function saveMutationKey(scope: SettingsScope): Array<string> {
  return ["saveSettings", ...settingsQueryKey(scope)]
}

/** The settings fields whose values are strings, for rows that pick one. */
export type StringSettingField = {
  [K in keyof WorkspaceSettings]-?: WorkspaceSettings[K] extends
    | string
    | null
    | undefined
    ? K
    : never
}[keyof WorkspaceSettings]

export interface ScopedSettings {
  scope: SettingsScope
  /** What runs in this scope see, including saves still in flight; undefined until loaded. */
  data: WorkspaceSettings | undefined
  /** What the server last confirmed, for drafts that must survive a failed save. */
  saved: WorkspaceSettings | undefined
  isPending: boolean
  /** Whether every one of `fields` comes from the tier above; always false on the instance. */
  inherits: (...fields: Array<keyof WorkspaceSettings>) => boolean
  /** Sets `patch` on this scope's own record. */
  save: (patch: WorkspaceSettingsOverrides) => void
  /** Drops `fields` from a workspace's record so they inherit again; no-op on the instance. */
  reset: (...fields: Array<keyof WorkspaceSettings>) => void
  error: string | null
}

interface Snapshot {
  effective: WorkspaceSettings
  overrides: WorkspaceSettingsOverrides
}

async function load(scope: SettingsScope): Promise<Snapshot> {
  if (scope.kind === "instance") {
    return { effective: await api.getInstanceSettings(), overrides: {} }
  }
  return api.getWorkspaceSettings(scope.slug)
}

interface SettingsEdit {
  scope: SettingsScope
  set: WorkspaceSettingsOverrides
  clear: Array<keyof WorkspaceSettings>
}

function applyEdit(snapshot: Snapshot, edit: SettingsEdit): Snapshot {
  const overrides = { ...snapshot.overrides, ...edit.set }
  for (const field of edit.clear) delete overrides[field]
  return { effective: { ...snapshot.effective, ...edit.set }, overrides }
}

async function persist(
  scope: SettingsScope,
  next: Snapshot
): Promise<Snapshot> {
  if (scope.kind === "instance") {
    const saved = await api.saveInstanceSettings(next.effective)
    return { effective: saved, overrides: {} }
  }
  const view: WorkspaceSettingsView = await api.saveWorkspaceSettings(
    scope.slug,
    next.overrides
  )
  return view
}

/**
 * Reads and writes the settings record for `scope`.
 *
 * The instance record is written whole. A workspace's record holds only the
 * fields an admin set there, so a save sends the current overrides plus the
 * patch, and a reset sends them minus the fields. The scope travels in the
 * mutation variables because TanStack Query rebinds a pending mutation's
 * callbacks to the latest render: a save started before the admin navigated
 * to another workspace must not land in that workspace's cache.
 *
 * Every section edits the same record through its own instance of this hook,
 * so saves run one at a time per scope and each builds its request from the
 * server's latest answer; the cache only ever holds confirmed data, and
 * pending edits are layered on top for display, so a failed save drops just
 * its own edit.
 */
export function useScopedSettings(
  scope: SettingsScope,
  onSaved?: () => void
): ScopedSettings {
  const qc = useQueryClient()
  const [failure, setFailure] = useState<{
    key: string
    message: string
  } | null>(null)
  const snapshot = useQuery({
    queryKey: settingsQueryKey(scope),
    queryFn: () => load(scope),
  })
  const mutationKey = saveMutationKey(scope)
  const scopeId = JSON.stringify(mutationKey)
  const mutation = useMutation({
    mutationKey,
    scope: { id: scopeId },
    mutationFn: (edit: SettingsEdit) => {
      const base = qc.getQueryData<Snapshot>(settingsQueryKey(edit.scope))
      if (!base) throw new Error("Settings are not loaded.")
      return persist(edit.scope, applyEdit(base, edit))
    },
    onMutate: async (edit) => {
      setFailure(null)
      await qc.cancelQueries({ queryKey: settingsQueryKey(edit.scope) })
    },
    onSuccess: (saved, edit) => {
      qc.setQueryData(settingsQueryKey(edit.scope), saved)
      onSaved?.()
    },
    onError: (e: Error, edit) =>
      setFailure({
        key: JSON.stringify(saveMutationKey(edit.scope)),
        message: e.message,
      }),
    onSettled: async (_data, _error, edit) => {
      if (qc.isMutating({ mutationKey: saveMutationKey(edit.scope) }) > 1)
        return
      await Promise.all([
        qc.invalidateQueries({ queryKey: settingsQueryKey(edit.scope) }),
        edit.scope.kind === "instance" &&
          qc.invalidateQueries({ queryKey: ["workspaceSettings"] }),
      ])
    },
  })
  const pendingEdits = useMutationState({
    filters: { mutationKey, exact: true, status: "pending" },
    select: (m) => m.state.variables as SettingsEdit,
  })

  const confirmed = snapshot.data
  const current = confirmed && pendingEdits.reduce(applyEdit, confirmed)
  const write = (
    set: WorkspaceSettingsOverrides,
    clear: SettingsEdit["clear"]
  ) => mutation.mutate({ scope, set, clear })

  return {
    scope,
    data: current?.effective,
    saved: confirmed?.effective,
    isPending: snapshot.isPending,
    inherits: (...fields) =>
      scope.kind === "workspace" &&
      current !== undefined &&
      fields.every((field) => !(field in current.overrides)),
    save: (patch) => {
      if (!current) return
      write(patch, [])
    },
    reset: (...fields) => {
      if (!current || scope.kind !== "workspace") return
      write({}, fields)
    },
    error: failure?.key === scopeId ? failure.message : null,
  }
}
