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
  const session = useSession()
  const models = optionsQuery.data?.models ?? []
  const defaultSelection = optionsQuery.data
    ? storedSelection(models, session.data?.login ?? "")
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
  selection: ModelSelection | null,
  /** Route/model the router picked for this run, shown for Auto. */
  routed?: { route?: string; modelId?: string | null } | null
): string {
  if (!selection) {
    const route = routed?.route
    if (route === "fast" || route === "balanced" || route === "performance") {
      return `Auto ${formatRoute(route)}`
    }
    return "Auto"
  }
  const model = models.find((m) => m.id === selection.modelId)
  const modelLabel = model?.label ?? selection.modelId
  return `${modelLabel} ${formatEffort(selection.effort)}`
}

const ROUTE_LABELS: Record<"fast" | "balanced" | "performance", string> = {
  fast: "Fast",
  balanced: "Balanced",
  performance: "Performance",
}

export function formatRoute(
  route: "fast" | "balanced" | "performance"
): string {
  return ROUTE_LABELS[route]
}

/** The model label behind the Auto router's latest pick, for hover display. */
export function routedModelLabel(
  models: Array<ModelOption>,
  routed?: { modelId?: string | null } | null
): string | null {
  const model = routed?.modelId
    ? models.find((m) => m.id === routed.modelId)
    : undefined
  return model?.label ?? null
}
