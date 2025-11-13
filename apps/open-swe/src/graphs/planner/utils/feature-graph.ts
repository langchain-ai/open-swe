import path from "node:path";
import { FeatureGraph, loadFeatureGraph } from "@openswe/shared/feature-graph";
import type { FeatureNode } from "@openswe/shared/feature-graph/types";
import type { NeighborDirection } from "@openswe/shared/feature-graph/graph";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "PlannerFeatureGraph");

const FEATURE_GRAPH_RELATIVE_PATH = path.join(
  "features",
  "graph",
  "graph.yaml",
);

let cachedWorkspacePath: string | undefined;
let cachedGraph: FeatureGraph | undefined;

async function loadPlannerFeatureGraph(
  workspacePath: string,
): Promise<FeatureGraph | undefined> {
  if (cachedGraph && cachedWorkspacePath === workspacePath) {
    return cachedGraph;
  }

  const graphPath = path.join(workspacePath, FEATURE_GRAPH_RELATIVE_PATH);

  try {
    const data = await loadFeatureGraph(graphPath);
    cachedWorkspacePath = workspacePath;
    cachedGraph = new FeatureGraph(data);
    return cachedGraph;
  } catch (error) {
    const details =
      error instanceof Error
        ? { message: error.message }
        : { error: String(error) };
    logger.warn("Unable to load feature graph", {
      workspacePath,
      ...details,
    });
    cachedWorkspacePath = undefined;
    cachedGraph = undefined;
    return undefined;
  }
}

type ResolutionOptions = {
  workspacePath?: string;
  featureIds?: string[];
};

export async function resolveActiveFeatures({
  workspacePath,
  featureIds,
}: ResolutionOptions): Promise<FeatureNode[]> {
  if (!workspacePath || !featureIds?.length) {
    return [];
  }

  const graph = await loadPlannerFeatureGraph(workspacePath);
  if (!graph) {
    return [];
  }

  const resolved: FeatureNode[] = [];
  for (const featureId of featureIds) {
    const trimmed = featureId.trim();
    if (!trimmed) continue;
    const feature = graph.getFeature(trimmed);
    if (feature) {
      resolved.push(feature);
    } else {
      logger.warn("Active feature not found in graph", { featureId: trimmed });
    }
  }

  return resolved;
}

export async function resolveFeatureDependencies({
  workspacePath,
  featureIds,
  direction = "upstream",
}: ResolutionOptions & { direction?: NeighborDirection }): Promise<FeatureNode[]> {
  if (!workspacePath || !featureIds?.length) {
    return [];
  }

  const graph = await loadPlannerFeatureGraph(workspacePath);
  if (!graph) {
    return [];
  }

  const activeIds = new Set(
    featureIds.map((featureId) => featureId.trim()).filter((id) => id.length > 0),
  );
  const dependencies = new Map<string, FeatureNode>();

  for (const featureId of activeIds) {
    if (!graph.hasFeature(featureId)) {
      logger.warn("Unable to look up dependencies for unknown feature", {
        featureId,
      });
      continue;
    }

    for (const neighbor of graph.getNeighbors(featureId, direction)) {
      if (activeIds.has(neighbor.id) || dependencies.has(neighbor.id)) continue;
      dependencies.set(neighbor.id, neighbor);
    }
  }

  return Array.from(dependencies.values());
}

// formatFeatureContext is provided by the shared feature-graph package
