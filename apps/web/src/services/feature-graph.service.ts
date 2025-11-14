import { Client } from "@langchain/langgraph-sdk";
import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import type {
  ArtifactCollection,
  ArtifactRef,
  FeatureEdge,
  FeatureNode,
} from "@openswe/shared/feature-graph/types";
import { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";

import { createClient } from "@/providers/client";

type SerializedFeatureGraph = {
  version?: number;
  nodes?: unknown;
  edges?: unknown;
  artifacts?: unknown;
};

export interface FeatureGraphFetchResult {
  graph: FeatureGraph | null;
  activeFeatureIds: string[];
}

export async function fetchFeatureGraph(
  threadId: string,
  client?: Client<ManagerGraphState>,
): Promise<FeatureGraphFetchResult> {
  if (!threadId) {
    throw new Error("Thread id is required to fetch feature graph data");
  }

  const resolvedClient = client ?? createClient(getApiUrl());

  const thread = await resolvedClient.threads.get<ManagerGraphState>(threadId);
  const graph = coerceFeatureGraph(thread?.values?.featureGraph);
  const activeFeatureIds = normalizeFeatureIds(thread?.values?.activeFeatureIds);

  return {
    graph,
    activeFeatureIds,
  };
}

function getApiUrl(): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  if (!apiUrl) {
    throw new Error("API URL not configured");
  }
  return apiUrl;
}

function coerceFeatureGraph(value: unknown): FeatureGraph | null {
  if (!value) return null;

  if (isFeatureGraphInstance(value)) {
    return value;
  }

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
  if (!value || typeof value !== "object") {
    return null;
  }

  if ("data" in value && typeof value.data === "object" && value.data) {
    return value.data as SerializedFeatureGraph;
  }

  return value as SerializedFeatureGraph;
}

function isFeatureGraphInstance(value: unknown): value is FeatureGraph {
  return (
    typeof value === "object" &&
    value !== null &&
    "listFeatures" in value &&
    typeof (value as FeatureGraph).listFeatures === "function" &&
    typeof (value as FeatureGraph).listEdges === "function"
  );
}

function coerceFeatureNodeMap(
  value: unknown,
): Map<string, FeatureNode> | null {
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

function normalizeFeatureIds(value: unknown): string[] {
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

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
