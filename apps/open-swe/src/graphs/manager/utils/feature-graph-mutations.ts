import path from "node:path";
import { writeFeatureGraphFile } from "@openswe/shared/feature-graph/writer";
import { FeatureGraph } from "@openswe/shared/feature-graph";
import { FeatureNode } from "@openswe/shared/feature-graph/types";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { FEATURE_GRAPH_RELATIVE_PATH } from "./feature-graph-path.js";

const logger = createLogger(LogLevel.INFO, "FeatureGraphMutations");

const cloneNodeWithStatus = (
  node: FeatureNode,
  status: string,
): FeatureNode => ({
  ...node,
  status,
});

export const applyFeatureStatus = (
  graph: FeatureGraph,
  featureId: string,
  status: string,
): FeatureGraph => {
  const serialized = graph.toJSON();
  const nodes = new Map(serialized.nodes);
  const node = nodes.get(featureId);

  if (!node) {
    throw new Error(`Feature ${featureId} not found in feature graph`);
  }

  nodes.set(featureId, cloneNodeWithStatus(node, status));

  return new FeatureGraph({
    version: serialized.version,
    nodes,
    edges: serialized.edges,
    artifacts: serialized.artifacts,
  });
};

export const featureGraphToFile = (graph: FeatureGraph) => {
  const serialized = graph.toJSON();

  return {
    version: serialized.version,
    nodes: serialized.nodes.map(([, node]) => node),
    edges: serialized.edges,
    ...(serialized.artifacts ? { artifacts: serialized.artifacts } : {}),
  };
};

export const persistFeatureGraph = async (
  graph: FeatureGraph,
  workspacePath: string | undefined,
): Promise<void> => {
  if (!workspacePath) return;

  const graphFile = featureGraphToFile(graph);
  const graphPath = path.join(workspacePath, FEATURE_GRAPH_RELATIVE_PATH);

  try {
    await writeFeatureGraphFile({
      graph: graphFile,
      outPath: graphPath,
    });
    logger.info("Persisted feature graph update", { graphPath });
  } catch (error) {
    logger.error("Failed to persist feature graph", {
      error: error instanceof Error ? error.message : String(error),
      graphPath,
    });
  }
};
