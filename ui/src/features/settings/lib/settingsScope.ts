import {
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import {
  api,
  type WorkspaceSettings,
  type WorkspaceSettingsOverrides,
} from "@/lib/api"

export type StringSettingField = {
  [K in keyof WorkspaceSettings]-?: WorkspaceSettings[K] extends
    | string
    | null
    | undefined
    ? K
    : never
}[keyof WorkspaceSettings]

export interface InstanceSettings {
  data: WorkspaceSettings | undefined
  saved: WorkspaceSettings | undefined
  save: (patch: WorkspaceSettingsOverrides) => void
}

const QUERY_KEY = ["instanceSettings"]
const MUTATION_KEY = ["saveSettings", ...QUERY_KEY]

/** Serialize section saves against confirmed settings and overlay pending edits. */
export function useInstanceSettings(): InstanceSettings {
  const qc = useQueryClient()
  const snapshot = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.getInstanceSettings,
  })
  const mutation = useMutation({
    mutationKey: MUTATION_KEY,
    scope: { id: JSON.stringify(MUTATION_KEY) },
    meta: { errorTitle: "Couldn't save settings" },
    mutationFn: (patch: WorkspaceSettingsOverrides) => {
      const base = qc.getQueryData<WorkspaceSettings>(QUERY_KEY)
      if (!base) throw new Error("Settings are not loaded.")
      return api.saveInstanceSettings({ ...base, ...patch })
    },
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: QUERY_KEY })
    },
    onSuccess: (saved) => {
      qc.setQueryData(QUERY_KEY, saved)
    },
    onSettled: async () => {
      if (qc.isMutating({ mutationKey: MUTATION_KEY }) > 1) return
      await Promise.all([
        qc.invalidateQueries({ queryKey: QUERY_KEY }),
        qc.invalidateQueries({ queryKey: ["options"] }),
        qc.invalidateQueries({ queryKey: ["workspaceSettings"] }),
      ])
    },
  })
  const pendingEdits = useMutationState({
    filters: { mutationKey: MUTATION_KEY, exact: true, status: "pending" },
    select: (m) => m.state.variables as WorkspaceSettingsOverrides,
  })
  const confirmed = snapshot.data
  const current = confirmed
    ? pendingEdits.reduce<WorkspaceSettings>(
        (data, patch) => ({ ...data, ...patch }),
        confirmed
      )
    : undefined

  return {
    data: current,
    saved: confirmed,
    save: (patch) => {
      if (current) mutation.mutate(patch)
    },
  }
}
