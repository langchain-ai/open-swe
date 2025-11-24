import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import type {
  ArtifactCollection,
  FeatureEdge,
  FeatureNode,
} from "@openswe/shared/feature-graph/types";
import type {
  FeatureProposal,
  FeatureProposalState,
} from "@openswe/shared/open-swe/manager/types";

export type FeatureGraphFetchResult = {
  graph: FeatureGraph | null;
  activeFeatureIds: string[];
  proposals: FeatureProposal[];
  activeProposalId: string | null;
  message?: string | null;
};

export function mapFeatureGraphPayload(
  data: unknown,
): FeatureGraphFetchResult {
  const graph = coerceGeneratedGraph(getGraphPayload(data));
  const activeFeatureIds = normalizeFeatureIds(
    getActiveFeatureIdsPayload(data),
  );
  const { proposals, activeProposalId } = mapFeatureProposalState(
    getFeatureProposalsPayload(data),
    getActiveProposalIdPayload(data),
  );

  const message = getMessagePayload(data);

  return {
    graph,
    activeFeatureIds,
    proposals,
    activeProposalId,
    message,
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

export function mapFeatureProposalState(
  state: unknown,
  activeProposalId?: unknown,
): Pick<FeatureGraphFetchResult, "proposals" | "activeProposalId"> {
  if (!state || typeof state !== "object") {
    return { proposals: [], activeProposalId: normalizeProposalId(activeProposalId) };
  }

  const proposalsSource =
    "proposals" in state && Array.isArray((state as FeatureProposalState).proposals)
      ? (state as FeatureProposalState).proposals
      : Array.isArray(state)
        ? (state as FeatureProposal[])
        : [];

  const proposals = proposalsSource
    .map((proposal) => normalizeProposal(proposal))
    .filter((proposal): proposal is FeatureProposal => Boolean(proposal));

  const resolvedActiveProposalId = normalizeProposalId(
    activeProposalId ?? (state as FeatureProposalState).activeProposalId,
  );

  return {
    proposals,
    activeProposalId: resolvedActiveProposalId,
  };
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

function getFeatureProposalsPayload(data: unknown): unknown {
  if (!data || typeof data !== "object") return null;

  if ("feature_proposals" in data) {
    return (data as Record<string, unknown>)["feature_proposals"];
  }

  if ("featureProposals" in data) {
    return (data as Record<string, unknown>)["featureProposals"];
  }

  if ("proposals" in data) {
    return data;
  }

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

function getActiveProposalIdPayload(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;

  const candidates = (() => {
    if ("active_proposal_id" in data) {
      return (data as Record<string, unknown>)["active_proposal_id"];
    }
    if ("activeProposalId" in data) {
      return (data as Record<string, unknown>)["activeProposalId"];
    }
    return null;
  })();

  return normalizeProposalId(candidates);
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

function normalizeProposal(value: unknown): FeatureProposal | null {
  if (!value || typeof value !== "object") return null;

  const { proposalId, featureId, summary, status, rationale, updatedAt } =
    value as FeatureProposal;

  if (
    typeof proposalId !== "string" ||
    typeof featureId !== "string" ||
    typeof summary !== "string" ||
    typeof status !== "string" ||
    typeof updatedAt !== "string"
  ) {
    return null;
  }

  if (!isValidProposalStatus(status)) {
    return null;
  }

  const normalized: FeatureProposal = {
    proposalId: proposalId.trim(),
    featureId: featureId.trim(),
    summary: summary.trim(),
    status,
    updatedAt,
  };

  if (typeof rationale === "string" && rationale.trim()) {
    normalized.rationale = rationale.trim();
  }

  return normalized;
}

function isValidProposalStatus(status: string): status is FeatureProposal["status"] {
  return status === "proposed" || status === "approved" || status === "rejected";
}

function normalizeProposalId(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function getMessagePayload(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const value = (data as Record<string, unknown>).message;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
