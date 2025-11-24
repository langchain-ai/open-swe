export type ThreadFlowStageId = "feature-graph" | "planner" | "programmer";

export type ThreadFlowStageStatus =
  | "pending"
  | "running"
  | "ready"
  | "blocked"
  | "completed"
  | "error";

export interface ThreadFlowStage {
  id: ThreadFlowStageId;
  label: string;
  status: ThreadFlowStageStatus;
  description?: string;
}

export interface ThreadFlowMetadata {
  flowType: "feature_graph_planner_programmer";
  entryPoint?: string;
  stages: ThreadFlowStage[];
}

export const createDefaultFlowStages = (): ThreadFlowStage[] => [
  { id: "feature-graph", label: "Feature Graph", status: "running" },
  { id: "planner", label: "Planner", status: "pending" },
  { id: "programmer", label: "Programmer", status: "pending" },
];

export const createThreadFlowMetadata = (entryPoint?: string): ThreadFlowMetadata => ({
  flowType: "feature_graph_planner_programmer",
  entryPoint,
  stages: createDefaultFlowStages(),
});

export const isThreadFlowMetadata = (
  value: unknown,
): value is ThreadFlowMetadata => {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ThreadFlowMetadata>;
  return candidate.flowType === "feature_graph_planner_programmer";
};
