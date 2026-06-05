import type { ModelOption } from "@/lib/api"
import { useOptions, useProfile } from "@/lib/profile"

export interface ModelSelection {
  modelId: string
  effort: string
}

export interface ModelOptionsResult {
  models: Array<ModelOption>
  defaultSelection: ModelSelection | null
  isLoading: boolean
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

  const models = optionsQuery.data?.models ?? []
  const profile = profileQuery.data
  const profileSelection = toSupportedSelection(
    models,
    profile?.default_model,
    profile?.reasoning_effort
  )
  const teamDefaultSelection = toSupportedSelection(
    models,
    optionsQuery.data?.default_agent_model,
    optionsQuery.data?.default_agent_reasoning_effort
  )
  const firstModel = models[0]
  const firstSelection = firstModel
    ? { modelId: firstModel.id, effort: firstModel.default_effort }
    : null
  const defaultSelection =
    optionsQuery.data && !profileQuery.isLoading
      ? (profileSelection ?? teamDefaultSelection ?? firstSelection)
      : null

  return {
    models,
    defaultSelection,
    isLoading: optionsQuery.isLoading || profileQuery.isLoading,
  }
}

const EFFORT_LABELS: Record<string, string> = {
  none: "None",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "XHigh",
  max: "Max",
}

export function formatModelSelection(
  models: Array<ModelOption>,
  selection: ModelSelection | null
): string {
  if (!selection) return "Default"
  const model = models.find((m) => m.id === selection.modelId)
  const modelLabel = model?.label ?? selection.modelId
  const effortLabel = EFFORT_LABELS[selection.effort] ?? selection.effort
  return `${modelLabel} ${effortLabel}`
}
