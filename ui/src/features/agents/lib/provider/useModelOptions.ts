import type { ModelOption } from "@/lib/api"
import { useOptions } from "@/lib/profile"

export interface ModelSelection {
  modelId: string
  effort: string
}

export interface ModelOptionsResult {
  models: Array<ModelOption>
  isLoading: boolean
}

/** ``workspace`` is the workspace being composed in, whose models these are. */
export function useModelOptions(workspace?: string | null): ModelOptionsResult {
  const optionsQuery = useOptions(workspace)

  return {
    models: optionsQuery.data?.models ?? [],
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
