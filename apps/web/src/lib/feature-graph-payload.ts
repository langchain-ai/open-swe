import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import type {
  ArtifactCollection,
  FeatureEdge,
  FeatureNode,
} from "@openswe/shared/feature-graph/types";

export type FeatureGraphFetchResult = {
  graph: FeatureGraph | null;
  activeFeatureIds: string[];
};

export function mapFeatureGraphPayload(
  data: unknown,
): FeatureGraphFetchResult {
  const graph = coerceGeneratedGraph(getGraphPayload(data));
  const activeFeatureIds = normalizeFeatureIds(
    getActiveFeatureIdsPayload(data),
  );

  return {
    graph,
    activeFeatureIds,
  };
}

export function normalizeFeatureIds(
  value?: string[] | null,
): string[] {
  if (!Array.isArray(value)) return [];

  const seen = new Set<string>();
  const normalized: string[] = [];

  for (const entry of value) {
    if (typeof entry !== "string") continue;
    const trimmed = entry.trim();
    if (!trimmed) continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    normalized.push(trimmed);
  }

  return normalized;
}

function getGraphPayload(data: unknown) {
  if (!data || typeof data !== "object") return null;
  if ("feature_graph" in data)
    return (data as Record<string, unknown>)["feature_graph"];
  if ("featureGraph" in data)
    return (data as Record<string, unknown>)["featureGraph"];
  if ("graph" in data) return (data as Record<string, unknown>)["graph"];
  return null;
}

function getActiveFeatureIdsPayload(data: unknown): string[] | null {
  if (!data || typeof data !== "object") return null;

  const candidates = (() => {
    if ("active_feature_ids" in data) {
      return (data as Record<string, unknown>)["active_feature_ids"];
    }
    if ("activeFeatureIds" in data) {
      return (data as Record<string, unknown>)["activeFeatureIds"];
    }
    return null;
  })();

  if (!Array.isArray(candidates)) return null;

  return candidates.every((item) => typeof item === "string")
    ? candidates
    : null;
}

function coerceGeneratedGraph(value: unknown): FeatureGraph | null {
  if (!value || typeof value !== "object") return null;

  const payload = value as Record<string, unknown>;
  const version = typeof payload.version === "number" ? payload.version : 1;

  const nodes = coerceGeneratedNodes(payload.nodes);
  if (!nodes) return null;

  const edges = coerceGeneratedEdges(payload.edges);
  const artifacts = payload.artifacts as ArtifactCollection | undefined;

  try {
    return new FeatureGraph({
      version,
      nodes,
      edges,
      artifacts,
    });
  } catch {
    return null;
  }
}

function coerceGeneratedNodes(
  value: unknown,
): Map<string, FeatureNode> | null {
  if (!Array.isArray(value)) return null;

  const map = new Map<string, FeatureNode>();
  for (const candidate of value) {
    if (!candidate || typeof candidate !== "object") continue;
    const node = candidate as Record<string, unknown>;
    const id = node.id;
    const name = node.name;
    const description = node.description;
    const status = node.status;

    if (
      typeof id !== "string" ||
      typeof name !== "string" ||
      typeof description !== "string" ||
      typeof status !== "string"
    ) {
      continue;
    }

    let normalizedMetadata: Record<string, unknown> | undefined = isPlainObject(
      node.metadata,
    )
      ? { ...node.metadata }
      : undefined;

    if (typeof node.development_progress === "number") {
      if (normalizedMetadata) {
        normalizedMetadata.development_progress = node.development_progress;
      } else {
        normalizedMetadata = {
          development_progress: node.development_progress,
        };
      }
    }

    const normalizedNode: FeatureNode = {
      id,
      name,
      description,
      status,
    };

    if (typeof node.group === "string") {
      normalizedNode.group = node.group;
    }

    if (normalizedMetadata) {
      normalizedNode.metadata = normalizedMetadata;
    }

    if (node.artifacts) {
      normalizedNode.artifacts = node.artifacts as ArtifactCollection;
    }

    map.set(normalizedNode.id, normalizedNode);
  }

  return map.size > 0 ? map : null;
}

function coerceGeneratedEdges(value: unknown): FeatureEdge[] {
  if (!Array.isArray(value)) return [];

  const edges: FeatureEdge[] = [];

  for (const candidate of value) {
    if (!candidate || typeof candidate !== "object") continue;
    const edge = candidate as Record<string, unknown>;
    const source = edge.source;
    const target = edge.target;
    const type = edge.type;

    if (
      typeof source !== "string" ||
      typeof target !== "string" ||
      typeof type !== "string"
    ) {
      continue;
    }

    const normalizedEdge: FeatureEdge = { source, target, type };

    if (isPlainObject(edge.metadata)) {
      normalizedEdge.metadata = edge.metadata as Record<string, unknown>;
    }

    edges.push(normalizedEdge);
  }

  return edges;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
