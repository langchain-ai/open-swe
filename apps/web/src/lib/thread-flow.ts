import { Thread } from "@langchain/langgraph-sdk";
import { ThreadUIStatus } from "@/lib/schemas/thread-status";
import { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import {
  ThreadFlowMetadata,
  ThreadFlowStage,
  createDefaultFlowStages,
  isThreadFlowMetadata,
} from "@openswe/shared/open-swe/manager/flow-metadata";
import { getActivePlanItems } from "@openswe/shared/open-swe/tasks";

const resolveMetadata = (
  thread: Thread<ManagerGraphState>,
): ThreadFlowMetadata | null => {
  const metadata = thread.metadata as { flow?: unknown } | undefined;
  if (metadata?.flow && isThreadFlowMetadata(metadata.flow)) {
    return metadata.flow;
  }

  if (isThreadFlowMetadata(metadata)) {
    return metadata;
  }

  return null;
};

const arePlanTasksCompleted = (state: ManagerGraphState | undefined): boolean => {
  if (!state?.taskPlan) return false;
  try {
    const activeItems = getActivePlanItems(state.taskPlan);
    return activeItems.length > 0 && activeItems.every((item) => item.completed);
  } catch {
    return false;
  }
};

const resolveFeatureGraphStatus = (
  thread: Thread<ManagerGraphState>,
  status?: ThreadUIStatus,
): ThreadFlowStage["status"] => {
  if (status === "error") return "error";
  if (status === "running") return "running";
  if (thread.values?.featureGraph) return "completed";
  return "pending";
};

const resolvePlannerStatus = (
  thread: Thread<ManagerGraphState>,
  status?: ThreadUIStatus,
): ThreadFlowStage["status"] => {
  if (status === "error") return "error";
  if (arePlanTasksCompleted(thread.values)) return "completed";
  if (thread.values?.plannerSession) return status === "running" ? "running" : "ready";
  if (thread.values?.featureGraph) return "pending";
  return "blocked";
};

const resolveProgrammerStatus = (
  thread: Thread<ManagerGraphState>,
  status?: ThreadUIStatus,
): ThreadFlowStage["status"] => {
  if (status === "error") return "error";
  if (arePlanTasksCompleted(thread.values)) return "completed";
  if (thread.values?.programmerSession || thread.values?.plannerSession) {
    return status === "running" ? "running" : "ready";
  }
  return "pending";
};

export const deriveThreadFlowStages = (
  thread: Thread<ManagerGraphState>,
  status?: ThreadUIStatus,
): ThreadFlowStage[] => {
  const metadata = resolveMetadata(thread);
  const baseStages = metadata?.stages ?? createDefaultFlowStages();
  const stages = new Map<ThreadFlowStage["id"], ThreadFlowStage>();

  baseStages.forEach((stage) => {
    stages.set(stage.id, { ...stage });
  });

  const featureGraphStage = stages.get("feature-graph");
  if (featureGraphStage) {
    featureGraphStage.status = resolveFeatureGraphStatus(thread, status);
  }

  const plannerStage = stages.get("planner");
  if (plannerStage) {
    plannerStage.status = resolvePlannerStatus(thread, status);
  }

  const programmerStage = stages.get("programmer");
  if (programmerStage) {
    programmerStage.status = resolveProgrammerStatus(thread, status);
  }

  return Array.from(stages.values());
};
