import fs from "node:fs/promises";
import path from "node:path";
import {
  FeatureDependencyMap,
  FeatureGraph,
  reconcileFeatureGraph,
} from "@openswe/shared/feature-graph";
import {
  FeatureGraphFile,
  FeatureNode,
  featureGraphFileSchema,
} from "@openswe/shared/feature-graph/types";
import { writeFeatureGraphFile } from "@openswe/shared/feature-graph/writer";
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

const validateGraphFile = (graph: FeatureGraphFile): FeatureGraphFile => {
  const parsed = featureGraphFileSchema.parse(graph);

  const nodeIds = new Set<string>();
  for (const node of parsed.nodes) {
    if (!("id" in node)) continue;

    if (nodeIds.has(node.id)) {
      throw new Error(`Duplicate feature id detected: ${node.id}`);
    }

    nodeIds.add(node.id);
  }

  const edgeKeys = new Set<string>();
  for (const edge of parsed.edges) {
    if (!("source" in edge && "target" in edge && "type" in edge)) {
      continue;
    }

    const { source, target, type } = edge;
    if (!nodeIds.has(source) || !nodeIds.has(target)) {
      throw new Error(
        `Feature edge references unknown feature: ${source} -> ${target} (${type})`,
      );
    }

    const key = `${source}->${target}#${type}`;
    if (edgeKeys.has(key)) {
      throw new Error(`Duplicate feature edge detected: ${key}`);
    }
    edgeKeys.add(key);
  }

  return parsed;
};

export const createFeatureNode = async (
  graph: FeatureGraph,
  feature: { id: string; name: string; summary: string },
  workspacePath: string | undefined,
): Promise<FeatureGraph> => {
  if (graph.hasFeature(feature.id)) {
    throw new Error(`Feature ${feature.id} already exists in the graph`);
  }

  const serialized = graph.toJSON();
  const nodes = new Map(serialized.nodes);
  const newNode: FeatureNode = {
    id: feature.id,
    name: feature.name,
    description: feature.summary,
    status: "inactive",
    metadata: {},
  };
  nodes.set(feature.id, newNode);

  const updatedGraph = new FeatureGraph({
    version: serialized.version,
    nodes,
    edges: serialized.edges,
    artifacts: serialized.artifacts,
  });

  await persistFeatureGraph(updatedGraph, workspacePath);

  return updatedGraph;
};

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

export const featureGraphToFile = (graph: FeatureGraph): FeatureGraphFile => {
  const serialized = graph.toJSON();

  return {
    version: serialized.version,
    nodes: serialized.nodes.map(([, node]) => node),
    edges: serialized.edges,
    ...(serialized.artifacts ? { artifacts: serialized.artifacts } : {}),
  };
};

export const reconcileFeatureGraphDependencies = (
  graph: FeatureGraph,
): { graph: FeatureGraph; dependencyMap: FeatureDependencyMap } => {
  return reconcileFeatureGraph(graph);
};

export const persistFeatureGraph = async (
  graph: FeatureGraph,
  workspacePath: string | undefined,
): Promise<void> => {
  if (!workspacePath) return;

  const graphFile = validateGraphFile(featureGraphToFile(graph));
  const graphPath = path.join(workspacePath, FEATURE_GRAPH_RELATIVE_PATH);

  try {
    await fs.mkdir(path.dirname(graphPath), { recursive: true });
    await writeFeatureGraphFile({
      graphPath,
      version: graphFile.version,
      nodes: graphFile.nodes,
      edges: graphFile.edges,
      artifacts: graphFile.artifacts,
    });
    logger.info("Persisted feature graph update", { graphPath });
  } catch (error) {
    logger.error("Failed to persist feature graph", {
      error: error instanceof Error ? error.message : String(error),
      graphPath,
    });
  }
};
