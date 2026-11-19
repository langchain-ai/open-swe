import type { ModelOption } from "@/lib/api"
import { useOptions, useProfile } from "@/lib/profile"
import { useSession } from "@/lib/session"

export interface ModelSelection {
  modelId: string
  effort: string
}

export interface ModelOptionsResult {
  models: Array<ModelOption>
  defaultRepo: string | null
  defaultSelection: ModelSelection | null
  cloudFirstDesktopEnabled: boolean
  modelSelectionEnabled: boolean
  isLoading: boolean
  isError: boolean
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
    ) as (Partial<ModelSelection> & { login?: string; mode?: string }) | null
    if (selection?.login === login && selection.mode === "auto") return null
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
  selection: ModelSelection | null,
  login: string
): void {
  if (typeof window === "undefined" || !login) return
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(
        selection ? { ...selection, login } : { mode: "auto", login }
      )
    )
  } catch {}
}

export function useModelOptions(): ModelOptionsResult {
  const optionsQuery = useOptions()
  const profile = useProfile()
  const session = useSession()
  const models = optionsQuery.data?.models ?? []
  const defaultSelection = optionsQuery.data
    ? storedSelection(models, session.data?.login ?? "")
    : null

  return {
    models,
    defaultRepo: optionsQuery.data?.default_repo ?? null,
    defaultSelection,
    // On globally, or by the person's own opt-in under Experiments.
    cloudFirstDesktopEnabled:
      optionsQuery.data?.cloud_first_desktop_enabled === true ||
      profile.data?.experimental_cloud_first_desktop === true,
    modelSelectionEnabled: optionsQuery.data?.model_selection_enabled === true,
    isLoading: optionsQuery.isLoading || session.isLoading,
    isError: optionsQuery.isError && !optionsQuery.data,
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

const ROUTE_LABELS = new Map([
  ["fast", "Auto Fast"],
  ["balanced", "Auto Balanced"],
  ["performance", "Auto Performance"],
])

export function formatAutoModelSelection(route: string | undefined): string {
  return (route && ROUTE_LABELS.get(route)) || "Auto"
}
