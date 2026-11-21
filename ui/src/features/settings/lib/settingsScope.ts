import {
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { api, type RobinSettings, type RobinSettingsPatch } from "@/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"

export type StringSettingField = {
  [K in keyof RobinSettings]-?: RobinSettings[K] extends
    | string
    | null
    | undefined
    ? K
    : never
}[keyof RobinSettings]

export interface InstanceSettings {
  data: RobinSettings | undefined
  saved: RobinSettings | undefined
  save: (patch: RobinSettingsPatch) => void
}

const QUERY_KEY = ["instanceSettings"]
const MUTATION_KEY = ["saveSettings", ...QUERY_KEY]

/** Serialize section saves against confirmed settings and overlay pending edits. */
export function useInstanceSettings(): InstanceSettings {
  const qc = useQueryClient()
  const snapshot = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.getInstanceSettings,
    meta: { invalidatedBy: [invalidationTopic("settings")] },
  })
  const mutation = useMutation({
    mutationKey: MUTATION_KEY,
    scope: { id: JSON.stringify(MUTATION_KEY) },
    meta: { errorTitle: "Couldn't save settings" },
    mutationFn: (patch: RobinSettingsPatch) => {
      const base = qc.getQueryData<RobinSettings>(QUERY_KEY)
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
      ])
    },
  })
  const pendingEdits = useMutationState({
    filters: { mutationKey: MUTATION_KEY, exact: true, status: "pending" },
    select: (m) => m.state.variables as RobinSettingsPatch,
  })
  const confirmed = snapshot.data
  const current = confirmed
    ? pendingEdits.reduce<RobinSettings>(
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
