import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import type {
  ArtifactCollection,
  ArtifactRef,
  FeatureEdge,
  FeatureNode,
} from "@openswe/shared/feature-graph/types";

type SerializedFeatureGraph = {
  version?: number;
  nodes?: unknown;
  edges?: unknown;
  artifacts?: unknown;
};

export function coerceFeatureGraph(value: unknown): FeatureGraph | null {
  if (!value) return null;

  const payload = extractGraphPayload(value);
  if (!payload) return null;

  const nodes = coerceFeatureNodeMap(payload.nodes);
  if (!nodes) return null;

  const edges = coerceFeatureEdges(payload.edges);
  const artifacts = coerceArtifactCollection(payload.artifacts);
  const version = typeof payload.version === "number" ? payload.version : 1;

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

function extractGraphPayload(value: unknown): SerializedFeatureGraph | null {
  if (!isPlainObject(value)) {
    return null;
  }

  if ("data" in value) {
    const data = (value as { data?: unknown }).data;
    if (isPlainObject(data)) {
      return data as SerializedFeatureGraph;
    }

    return null;
  }

  return value as SerializedFeatureGraph;
}

function coerceFeatureNodeMap(value: unknown): Map<string, FeatureNode> | null {
  if (!value) return null;

  const map = new Map<string, FeatureNode>();

  if (value instanceof Map) {
    for (const [, node] of value) {
      const normalized = coerceFeatureNode(node);
      if (normalized) {
        map.set(normalized.id, normalized);
      }
    }
  } else if (Array.isArray(value)) {
    for (const entry of value) {
      if (Array.isArray(entry) && entry.length >= 2) {
        const [, node] = entry;
        const normalized = coerceFeatureNode(node);
        if (normalized) {
          map.set(normalized.id, normalized);
        }
        continue;
      }

      const normalized = coerceFeatureNode(entry);
      if (normalized) {
        map.set(normalized.id, normalized);
      }
    }
  } else if (isPlainObject(value)) {
    for (const candidate of Object.values(value)) {
      const normalized = coerceFeatureNode(candidate);
      if (normalized) {
        map.set(normalized.id, normalized);
      }
    }
  }

  return map.size > 0 ? map : null;
}

function coerceFeatureNode(value: unknown): FeatureNode | null {
  if (!isPlainObject(value)) return null;

  const { id, name, description, status } = value as FeatureNode;

  if (
    typeof id !== "string" ||
    typeof name !== "string" ||
    typeof description !== "string" ||
    typeof status !== "string"
  ) {
    return null;
  }

  const node: FeatureNode = {
    id,
    name,
    description,
    status,
  };

  if ("group" in value && typeof value.group === "string") {
    node.group = value.group;
  }

  if ("metadata" in value && isPlainObject(value.metadata)) {
    node.metadata = value.metadata as Record<string, unknown>;
  }

  if ("artifacts" in value) {
    const artifacts = coerceArtifactCollection(value.artifacts);
    if (artifacts) {
      node.artifacts = artifacts;
    }
  }

  return node;
}

function coerceFeatureEdges(value: unknown): FeatureEdge[] {
  if (!Array.isArray(value)) return [];

  const edges: FeatureEdge[] = [];
  for (const entry of value) {
    if (!isPlainObject(entry)) continue;
    const { source, target, type } = entry as FeatureEdge;
    if (
      typeof source === "string" &&
      typeof target === "string" &&
      typeof type === "string"
    ) {
      const edge: FeatureEdge = { source, target, type };
      if ("metadata" in entry && isPlainObject(entry.metadata)) {
        edge.metadata = entry.metadata as Record<string, unknown>;
      }
      edges.push(edge);
    }
  }

  return edges;
}

function coerceArtifactCollection(
  value: unknown,
): ArtifactCollection | undefined {
  if (!value) return undefined;

  if (Array.isArray(value)) {
    const artifacts: ArtifactRef[] = [];
    for (const entry of value) {
      const artifact = coerceArtifactRef(entry);
      if (artifact) artifacts.push(artifact);
    }
    return artifacts.length > 0 ? artifacts : undefined;
  }

  if (isPlainObject(value)) {
    const artifacts: Record<string, ArtifactRef> = {};
    for (const [key, entry] of Object.entries(value)) {
      const artifact = coerceArtifactRef(entry);
      if (artifact) {
        artifacts[key] = artifact;
      }
    }
    return Object.keys(artifacts).length > 0 ? artifacts : undefined;
  }

  return undefined;
}

function coerceArtifactRef(value: unknown): ArtifactRef | null {
  if (typeof value === "string") {
    return value.trim() ? value : null;
  }

  if (!isPlainObject(value)) return null;

  const artifact: ArtifactRef = {};

  if ("name" in value && typeof value.name === "string") {
    artifact.name = value.name;
  }
  if ("description" in value && typeof value.description === "string") {
    artifact.description = value.description;
  }
  if ("path" in value && typeof value.path === "string") {
    artifact.path = value.path;
  }
  if ("url" in value && typeof value.url === "string") {
    artifact.url = value.url;
  }
  if ("type" in value && typeof value.type === "string") {
    artifact.type = value.type;
  }
  if ("metadata" in value && isPlainObject(value.metadata)) {
    artifact.metadata = value.metadata as Record<string, unknown>;
  }

  return Object.keys(artifact).length > 0 ? artifact : null;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype ||
      Object.getPrototypeOf(value) === null)
  );
}
