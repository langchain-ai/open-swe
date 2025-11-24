import { FeatureGraph } from "./graph.js";
import type { FeatureEdge, FeatureNode } from "./types.js";

export type FeatureDependencyMap = Record<string, string[]>;

const buildEdgeKey = (edge: FeatureEdge) =>
  `${edge.source}->${edge.target}#${edge.type}`;

const isValidEdge = (
  edge: FeatureEdge,
  knownNodeIds: Set<string>,
): edge is FeatureEdge =>
  Boolean(
    edge &&
      typeof edge.source === "string" &&
      typeof edge.target === "string" &&
      typeof edge.type === "string" &&
      knownNodeIds.has(edge.source) &&
      knownNodeIds.has(edge.target),
  );

export function reconcileFeatureGraph(
  graph: FeatureGraph,
): { graph: FeatureGraph; dependencyMap: FeatureDependencyMap } {
  const serialized = graph.toJSON();
  const nodes = new Map(serialized.nodes);
  const nodeIds = new Set(nodes.keys());

  const seenEdges = new Set<string>();
  const reconciledEdges: FeatureEdge[] = [];

  for (const edge of serialized.edges) {
    if (!isValidEdge(edge, nodeIds)) continue;

    const key = buildEdgeKey(edge);
    if (seenEdges.has(key)) continue;
    seenEdges.add(key);
    reconciledEdges.push({ ...edge });
  }

  const dependencyMap: FeatureDependencyMap = {};
  for (const nodeId of nodeIds) {
    dependencyMap[nodeId] = [];
  }

  for (const edge of reconciledEdges) {
    dependencyMap[edge.source]?.push(edge.target);
    dependencyMap[edge.target]?.push(edge.source);
  }

  return {
    graph: new FeatureGraph({
      version: serialized.version,
      nodes,
      edges: reconciledEdges,
      artifacts: serialized.artifacts,
    }),
    dependencyMap,
  };
}

export function clarifyFeatureDescription(feature: FeatureNode): string {
  const description = feature.description?.trim();
  if (description) return description;

  const name = feature.name?.trim();
  if (name) return name;

  return feature.id;
}
