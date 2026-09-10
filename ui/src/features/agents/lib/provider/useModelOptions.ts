import { useCallback } from "react"

import type { ModelOption } from "@/lib/api"
import {
  buildProfileUpdate,
  useOptions,
  useProfile,
  useSaveProfile,
} from "@/lib/profile"

export interface ModelSelection {
  modelId: string
  effort: string
}

export interface ModelOptionsResult {
  models: Array<ModelOption>
  /** `null` means adaptive routing ("Auto"). */
  defaultSelection: ModelSelection | null
  isLoading: boolean
  /** Persist the picker choice as the user's profile default; `null` re-enables Auto. */
  persistSelection: (next: ModelSelection | null) => void
}

function toSupportedSelection(
  models: Array<ModelOption>,
  modelId?: string | null,
  effort?: string | null
): ModelSelection | null {
  if (!modelId || !effort) return null
  const supported = models.some(
    (model) => model.id === modelId && model.efforts.includes(effort)
  )
  return supported ? { modelId, effort } : null
}

export function useModelOptions(): ModelOptionsResult {
  const optionsQuery = useOptions()
  const profileQuery = useProfile()
  const saveProfile = useSaveProfile()
  const models = optionsQuery.data?.models ?? []
  const profile = profileQuery.data
  const routingEnabled = profile?.model_routing_enabled ?? true
  const teamDefault = toSupportedSelection(
    models,
    optionsQuery.data?.default_agent_model,
    optionsQuery.data?.default_agent_reasoning_effort
  )
  const firstModel = models[0]
  const fallbackSelection =
    teamDefault ??
    (firstModel
      ? { modelId: firstModel.id, effort: firstModel.default_effort }
      : null)
  const defaultSelection =
    optionsQuery.data && !routingEnabled
      ? (toSupportedSelection(
          models,
          profile?.default_model,
          profile?.reasoning_effort
        ) ?? fallbackSelection)
      : null

  const { mutate: save } = saveProfile
  const persistSelection = useCallback(
    (next: ModelSelection | null) => {
      save(
        buildProfileUpdate(
          profile,
          {
            model_routing_enabled: next === null,
            ...(next
              ? { default_model: next.modelId, reasoning_effort: next.effort }
              : {}),
          },
          fallbackSelection?.modelId ?? "",
          fallbackSelection?.effort ?? ""
        )
      )
    },
    [save, profile, fallbackSelection?.modelId, fallbackSelection?.effort]
  )

  return {
    models,
    defaultSelection,
    isLoading: optionsQuery.isLoading || profileQuery.isLoading,
    persistSelection,
  }
}

const EFFORT_LABELS: Record<string, string> = {
  none: "None",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra High",
  max: "Max",
}

export function formatEffort(effort: string): string {
  return EFFORT_LABELS[effort] ?? effort
}

export function formatModelSelection(
  models: Array<ModelOption>,
  selection: ModelSelection | null
): string {
  if (!selection) return "Auto"
  const model = models.find((m) => m.id === selection.modelId)
  const modelLabel = model?.label ?? selection.modelId
  return `${modelLabel} ${formatEffort(selection.effort)}`
}
