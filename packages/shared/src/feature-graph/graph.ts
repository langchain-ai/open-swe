import {
  ArtifactCollection,
  FeatureEdge,
  FeatureNode,
} from "./types.js";
import { FeatureGraphData } from "./loader.js";

export type NeighborDirection = "downstream" | "upstream" | "both";

type AdjacencyLists = {
  outgoing: Map<string, FeatureEdge[]>;
  incoming: Map<string, FeatureEdge[]>;
};

const cloneEdgeList = (edges: FeatureEdge[] | undefined): FeatureEdge[] =>
  edges ? [...edges] : [];

const cloneArtifacts = (
  artifacts: ArtifactCollection | undefined
): ArtifactCollection | undefined => {
  if (!artifacts) return undefined;
  return Array.isArray(artifacts)
    ? [...artifacts]
    : { ...artifacts };
};

export class FeatureGraph {
  readonly version: number;
  private readonly nodes: Map<string, FeatureNode>;
  private readonly edges: FeatureEdge[];
  private readonly artifacts?: ArtifactCollection;
  private adjacency?: AdjacencyLists;

  constructor(data: FeatureGraphData) {
    this.version = data.version;
    this.nodes = new Map(data.nodes);
    this.edges = [...data.edges];
    this.artifacts = cloneArtifacts(data.artifacts);
  }

  private ensureAdjacency(): void {
    if (this.adjacency) return;

    const outgoing = new Map<string, FeatureEdge[]>();
    const incoming = new Map<string, FeatureEdge[]>();

    for (const edge of this.edges) {
      if (!this.nodes.has(edge.source)) {
        throw new Error(
          `Feature edge references unknown source feature: ${edge.source}`
        );
      }

      if (!this.nodes.has(edge.target)) {
        throw new Error(
          `Feature edge references unknown target feature: ${edge.target}`
        );
      }

      const outgoingList = outgoing.get(edge.source);
      if (outgoingList) {
        outgoingList.push(edge);
      } else {
        outgoing.set(edge.source, [edge]);
      }

      const incomingList = incoming.get(edge.target);
      if (incomingList) {
        incomingList.push(edge);
      } else {
        incoming.set(edge.target, [edge]);
      }
    }

    this.adjacency = { outgoing, incoming };
  }

  getArtifacts(): ArtifactCollection | undefined {
    return this.artifacts;
  }

  getFeature(id: string): FeatureNode | undefined {
    return this.nodes.get(id);
  }

  hasFeature(id: string): boolean {
    return this.nodes.has(id);
  }

  listFeatures(): FeatureNode[] {
    return Array.from(this.nodes.values());
  }

  listEdges(): FeatureEdge[] {
    return [...this.edges];
  }

  getEdgesFrom(id: string): FeatureEdge[] {
    this.ensureAdjacency();
    return cloneEdgeList(this.adjacency?.outgoing.get(id));
  }

  getEdgesInto(id: string): FeatureEdge[] {
    this.ensureAdjacency();
    return cloneEdgeList(this.adjacency?.incoming.get(id));
  }

  getNeighbors(
    id: string,
    direction: NeighborDirection = "downstream"
  ): FeatureNode[] {
    this.ensureAdjacency();

    const seen = new Set<string>();
    const results: FeatureNode[] = [];

    const pushNeighbor = (neighborId: string) => {
      if (seen.has(neighborId)) return;
      const node = this.nodes.get(neighborId);
      if (!node) return;
      seen.add(neighborId);
      results.push(node);
    };

    if (direction === "downstream" || direction === "both") {
      for (const edge of this.adjacency?.outgoing.get(id) ?? []) {
        pushNeighbor(edge.target);
      }
    }

    if (direction === "upstream" || direction === "both") {
      for (const edge of this.adjacency?.incoming.get(id) ?? []) {
        pushNeighbor(edge.source);
      }
    }

    return results;
  }
}
