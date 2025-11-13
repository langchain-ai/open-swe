import { FeatureGraph } from "../graph.js";
import type { FeatureEdge, FeatureNode } from "../types.js";

describe("FeatureGraph", () => {
  const featureA: FeatureNode = {
    id: "feature-auth",
    name: "Authentication",
    description: "Handles sign-in flow",
    status: "active",
    artifacts: ["apps/web/src/auth/__tests__/login.test.ts"],
  };
  const featureB: FeatureNode = {
    id: "feature-profile",
    name: "Profile management",
    description: "User profile editing",
    status: "in-progress",
  };
  const featureC: FeatureNode = {
    id: "feature-audit",
    name: "Audit trail",
    description: "Compliance logging",
    status: "proposed",
  };

  const edges: FeatureEdge[] = [
    { source: featureA.id, target: featureB.id, type: "depends-on" },
    { source: featureB.id, target: featureC.id, type: "relates-to" },
    { source: featureC.id, target: featureA.id, type: "supports" },
  ];

  const createGraph = () =>
    new FeatureGraph({
      version: 1,
      nodes: new Map([
        [featureA.id, featureA],
        [featureB.id, featureB],
        [featureC.id, featureC],
      ]),
      edges,
      artifacts: {
        documentation: { path: "docs/features/authentication.md" },
      },
    });

  it("exposes features, edges, and artifacts without leaking internal state", () => {
    const graph = createGraph();

    expect(graph.version).toBe(1);
    expect(graph.hasFeature(featureB.id)).toBe(true);
    expect(graph.getFeature("missing")).toBeUndefined();

    const features = graph.listFeatures();
    expect(features.map((feature) => feature.id)).toEqual(
      expect.arrayContaining([featureA.id, featureB.id, featureC.id]),
    );
    features.push({
      id: "mutated",
      name: "Mutated",
      description: "",
      status: "inactive",
    });
    expect(graph.listFeatures()).toHaveLength(3);

    expect(graph.getArtifacts()).toEqual({
      documentation: { path: "docs/features/authentication.md" },
    });

    const edgeList = graph.listEdges();
    expect(edgeList).toHaveLength(3);
    edgeList.pop();
    expect(graph.listEdges()).toHaveLength(3);

    const outgoing = graph.getEdgesFrom(featureA.id);
    expect(outgoing).toHaveLength(1);
    outgoing.push({ source: featureA.id, target: "mutated", type: "test" });
    expect(graph.getEdgesFrom(featureA.id)).toHaveLength(1);

    const incoming = graph.getEdgesInto(featureB.id);
    expect(incoming).toHaveLength(1);
    incoming.pop();
    expect(graph.getEdgesInto(featureB.id)).toHaveLength(1);

    const neighbors = graph.getNeighbors(featureB.id, "both");
    expect(neighbors.map((feature) => feature.id)).toEqual(
      expect.arrayContaining([featureA.id, featureC.id]),
    );
  });

  it("deduplicates neighbors when traversing in multiple directions", () => {
    const graph = createGraph();
    const neighbors = graph.getNeighbors(featureA.id, "both");
    expect(neighbors.filter((neighbor) => neighbor.id === featureB.id)).toHaveLength(1);
  });

  it("throws when edges reference unknown nodes", () => {
    const graph = new FeatureGraph({
      version: 1,
      nodes: new Map([[featureA.id, featureA]]),
      edges: [
        { source: featureA.id, target: "missing", type: "invalid" },
      ],
    });

    expect(() => graph.getEdgesFrom(featureA.id)).toThrow(
      /unknown target feature/i,
    );
  });
});
