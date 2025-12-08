import { FeatureGraphData } from "./loader.js";
import { ArtifactCollection, FeatureEdge, FeatureNode } from "./types.js";

export type FeatureGraphJson = {
  version: number;
  nodes: FeatureNode[] | [string, FeatureNode][];
  edges: FeatureEdge[];
  artifacts?: ArtifactCollection;
};

export type ListFeaturesOptions = {
  activeFeatureIds?: Iterable<string>;
};

const isFeatureGraphData = (
  graph: FeatureGraphData | FeatureGraphJson,
): graph is FeatureGraphData => graph.nodes instanceof Map;

const isTupleNodeList = (
  nodes: FeatureGraphJson["nodes"],
): nodes is [string, FeatureNode][] =>
  Array.isArray(nodes) && nodes.length > 0 && Array.isArray(nodes[0]);

const getFeatureNodes = (
  graph: FeatureGraphData | FeatureGraphJson,
): FeatureNode[] => {
  if (isFeatureGraphData(graph)) {
    return Array.from(graph.nodes.values());
  }

  if (isTupleNodeList(graph.nodes)) {
    return graph.nodes.map(([, node]) => node);
  }

  return [...graph.nodes];
};

export const listFeaturesFromGraph = (
  graph: FeatureGraphData | FeatureGraphJson,
  options?: ListFeaturesOptions,
): FeatureNode[] => {
  const features = getFeatureNodes(graph);

  if (!options?.activeFeatureIds) {
    return features;
  }

  const nodeById = new Map(features.map((feature) => [feature.id, feature]));
  const orderedActiveIds = Array.from(options.activeFeatureIds).filter(
    (id) => typeof id === "string" && id.trim().length > 0,
  );

  if (!orderedActiveIds.length) {
    return features;
  }

  const filtered = orderedActiveIds
    .map((id) => nodeById.get(id))
    .filter((feature): feature is FeatureNode => Boolean(feature));

  return filtered.length > 0 ? filtered : features;
};
