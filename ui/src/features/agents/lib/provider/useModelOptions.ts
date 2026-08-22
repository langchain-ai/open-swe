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
const LOCAL_LOGIN = "local"
const LOCAL_MODELS: Array<ModelOption> = [
  {
    id: "openai:gpt-5.6-sol",
    label: "GPT-5.6",
    efforts: ["none", "low", "medium", "high", "xhigh"],
    default_effort: "high",
    supports_images: true,
  },
]

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
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ ...selection, login: login || LOCAL_LOGIN })
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
  const session = useSession()
  const localMode =
    typeof window !== "undefined" &&
    Boolean(window.openSweDesktop) &&
    !session.data
  const optionsQuery = useOptions(!localMode)
  const models = localMode ? LOCAL_MODELS : (optionsQuery.data?.models ?? [])
  const teamDefaultSelection = toSupportedSelection(
    models,
    optionsQuery.data?.default_agent_model,
    optionsQuery.data?.default_agent_reasoning_effort
  )
  const firstModel = models[0]
  const firstSelection = firstModel
    ? { modelId: firstModel.id, effort: firstModel.default_effort }
    : null
  const selectionLogin = session.data?.login ?? LOCAL_LOGIN
  const defaultSelection = localMode
    ? (storedSelection(models, selectionLogin) ?? firstSelection)
    : optionsQuery.data
      ? (storedSelection(models, session.data?.login ?? "") ??
        teamDefaultSelection ??
        firstSelection)
      : null

  return {
    models,
    defaultSelection,
    isLoading: localMode ? false : optionsQuery.isLoading || session.isLoading,
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
