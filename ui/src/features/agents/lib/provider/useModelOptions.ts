import type { ModelOption } from "@/lib/api"
import { useOptions } from "@/lib/profile"
import { useSession } from "@/lib/session"

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

function storedSelection(
  models: Array<ModelOption>,
  login: string
): ModelSelection | null {
  if (typeof window === "undefined" || !login) return null
  try {
    const selection = JSON.parse(
      window.localStorage.getItem(STORAGE_KEY) ?? "null"
    ) as (Partial<ModelSelection> & { login?: string }) | null
    return selection?.login === login &&
      models.some(
        (model) =>
          model.id === selection.modelId &&
          model.efforts.includes(selection.effort ?? "")
      )
      ? (selection as ModelSelection)
      : null
  } catch {
    return null
  }
}

export function persistModelSelection(
  selection: ModelSelection,
  login: string
): void {
  if (typeof window === "undefined" || !login) return
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ ...selection, login })
    )
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
  const session = useSession()
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
    ? (storedSelection(models, session.data?.login ?? "") ??
      teamDefaultSelection ??
      firstSelection)
    : null

  return {
    models,
    defaultSelection,
    isLoading: optionsQuery.isLoading || session.isLoading,
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
