import type { ModelOption } from "@/lib/api"
import { useOptions } from "@/lib/profile"

export interface ModelSelection {
  modelId: string
  effort: string
}

export interface ModelOptionsResult {
  models: Array<ModelOption>
  defaultSelection: ModelSelection | null
  isLoading: boolean
}

const STORAGE_KEY = "open-swe.agents.model-selection"

function storedSelection(models: Array<ModelOption>): ModelSelection | null {
  if (typeof window === "undefined") return null
  try {
    const selection = JSON.parse(
      window.localStorage.getItem(STORAGE_KEY) ?? "null"
    ) as Partial<ModelSelection> | null
    return models.some(
      (model) =>
        model.id === selection?.modelId &&
        model.efforts.includes(selection.effort ?? "")
    )
      ? (selection as ModelSelection)
      : null
  } catch {
    return null
  }
}

export function persistModelSelection(selection: ModelSelection): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(selection))
  } catch {}
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
  const models = optionsQuery.data?.models ?? []
  const teamDefaultSelection = toSupportedSelection(
    models,
    optionsQuery.data?.default_agent_model,
    optionsQuery.data?.default_agent_reasoning_effort
  )
  const firstModel = models[0]
  const firstSelection = firstModel
    ? { modelId: firstModel.id, effort: firstModel.default_effort }
    : null
  const defaultSelection = optionsQuery.data
    ? (storedSelection(models) ?? teamDefaultSelection ?? firstSelection)
    : null

  return {
    models,
    defaultSelection,
    isLoading: optionsQuery.isLoading,
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
  if (!selection) return "Default"
  const model = models.find((m) => m.id === selection.modelId)
  const modelLabel = model?.label ?? selection.modelId
  return `${modelLabel} ${formatEffort(selection.effort)}`
}
