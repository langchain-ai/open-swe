import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
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
  /** What runs in this scope see; undefined until loaded. */
  data: WorkspaceSettings | undefined
  isPending: boolean
  /** Whether every one of `fields` comes from the tier above; always false on the instance. */
  inherits: (...fields: Array<keyof WorkspaceSettings>) => boolean
  /** Sets `patch` on this scope's own record. */
  save: (patch: WorkspaceSettingsOverrides) => void
  /** Drops `fields` from a workspace's record so they inherit again; no-op on the instance. */
  reset: (...fields: Array<keyof WorkspaceSettings>) => void
  saving: boolean
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

interface SaveVariables {
  scope: SettingsScope
  effective: WorkspaceSettings
  overrides: WorkspaceSettingsOverrides
}

async function persist(variables: SaveVariables): Promise<Snapshot> {
  if (variables.scope.kind === "instance") {
    const saved = await api.saveInstanceSettings(variables.effective)
    return { effective: saved, overrides: {} }
  }
  const view: WorkspaceSettingsView = await api.saveWorkspaceSettings(
    variables.scope.slug,
    variables.overrides
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
 */
export function useScopedSettings(
  scope: SettingsScope,
  onSaved?: () => void
): ScopedSettings {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const snapshot = useQuery({
    queryKey: settingsQueryKey(scope),
    queryFn: () => load(scope),
  })
  const mutation = useMutation({
    mutationFn: persist,
    onSuccess: (saved, variables) => {
      qc.setQueryData(settingsQueryKey(variables.scope), saved)
      setError(null)
      onSaved?.()
    },
    onError: (e: Error) => setError(e.message),
  })

  const current = snapshot.data
  const write = (
    overrides: WorkspaceSettingsOverrides,
    effective: WorkspaceSettings
  ) => mutation.mutate({ scope, effective, overrides })

  return {
    scope,
    data: current?.effective,
    isPending: snapshot.isPending,
    inherits: (...fields) =>
      scope.kind === "workspace" &&
      current !== undefined &&
      fields.every((field) => !(field in current.overrides)),
    save: (patch) => {
      if (!current) return
      write(
        { ...current.overrides, ...patch },
        { ...current.effective, ...patch }
      )
    },
    reset: (...fields) => {
      if (!current || scope.kind !== "workspace") return
      const overrides = { ...current.overrides }
      for (const field of fields) delete overrides[field]
      write(overrides, current.effective)
    },
    saving: mutation.isPending,
    error,
  }
}
