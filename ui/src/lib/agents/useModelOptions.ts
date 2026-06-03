import { useQuery } from "@tanstack/react-query";

import { api, type ModelOption } from "@/lib/api";

export interface ModelSelection {
  modelId: string;
  effort: string;
}

export interface ModelOptionsResult {
  models: ModelOption[];
  defaultSelection: ModelSelection | null;
  isLoading: boolean;
}

export function useModelOptions(): ModelOptionsResult {
  const optionsQuery = useQuery({
    queryKey: ["dashboard", "options"],
    queryFn: () => api.options(),
    staleTime: 5 * 60_000,
  });
  const profileQuery = useQuery({
    queryKey: ["dashboard", "profile"],
    queryFn: () => api.profile(),
    staleTime: 5 * 60_000,
  });

  const models = optionsQuery.data?.models ?? [];
  const profile = profileQuery.data;

  let defaultSelection: ModelSelection | null = null;
  const profileModelId = profile?.default_model;
  const profileEffort = profile?.reasoning_effort;
  if (
    profileModelId &&
    profileEffort &&
    models.some((m) => m.id === profileModelId && m.efforts.includes(profileEffort))
  ) {
    defaultSelection = { modelId: profileModelId, effort: profileEffort };
  } else if (models.length > 0) {
    const first = models[0]!;
    defaultSelection = { modelId: first.id, effort: first.default_effort };
  }

  return {
    models,
    defaultSelection,
    isLoading: optionsQuery.isLoading || profileQuery.isLoading,
  };
}

const EFFORT_LABELS: Record<string, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "XHigh",
  max: "Max",
};

export function formatModelSelection(
  models: ModelOption[],
  selection: ModelSelection | null,
): string {
  if (!selection) return "Default";
  const model = models.find((m) => m.id === selection.modelId);
  const modelLabel = model?.label ?? selection.modelId;
  const effortLabel = EFFORT_LABELS[selection.effort] ?? selection.effort;
  return `${modelLabel} ${effortLabel}`;
}
