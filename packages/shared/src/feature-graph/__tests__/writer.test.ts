import {
  renderFeatureGraphYaml,
  upsertFeatureEdgeEntry,
  upsertFeatureNodeEntry,
} from "../writer.js";
import type {
  FeatureEdgeEntry,
  FeatureGraphFile,
  FeatureNodeEntry,
} from "../types.js";

describe("feature graph writer", () => {
  it("upserts feature node entries by identity", () => {
    const initial: FeatureNodeEntry[] = [
      { source: "./nodes/auth.yaml" },
      { manifest: "./nodes/common.yaml" },
    ];

    const firstInsert = upsertFeatureNodeEntry(initial, {
      id: "feature-login",
      name: "Login",
      description: "Handles authentication",
      status: "active",
    });

    expect(firstInsert).toHaveLength(3);
    const updated = upsertFeatureNodeEntry(firstInsert, {
      id: "feature-login",
      name: "Login",
      description: "Handles auth securely",
      status: "active",
      group: "auth",
    });

    expect(updated).toHaveLength(3);
    expect(updated.find((entry) => "id" in entry && entry.id === "feature-login"))
      .toEqual({
        id: "feature-login",
        name: "Login",
        description: "Handles auth securely",
        status: "active",
        group: "auth",
      });
  });

  it("upserts feature edge entries by identity", () => {
    const initial: FeatureEdgeEntry[] = [
      { source: "./edges/core.yaml" },
      { manifest: "./edges/derived.yaml" },
    ];

    const afterInsert = upsertFeatureEdgeEntry(initial, {
      source: "feature-login",
      target: "feature-audit",
      type: "depends-on",
    });
    expect(afterInsert).toHaveLength(3);

    const updated = upsertFeatureEdgeEntry(afterInsert, {
      source: "feature-login",
      target: "feature-audit",
      type: "depends-on",
      metadata: { confidence: "medium" },
    });
    expect(updated).toHaveLength(3);
    expect(
      updated.find(
        (entry) =>
          "target" in entry &&
          entry.source === "feature-login" &&
          entry.target === "feature-audit",
      ),
    ).toEqual({
      source: "feature-login",
      target: "feature-audit",
      type: "depends-on",
      metadata: { confidence: "medium" },
    });
  });

  it("renders feature graphs with deterministic key ordering", () => {
    const graph: FeatureGraphFile = {
      version: 2,
      artifacts: [
        "apps/web/src/auth/__tests__/login.test.ts",
        { path: "apps/api/tests/auth.spec.ts", metadata: { suite: "api" } },
        { path: "docs/features/spec.md", description: "Spec" },
      ],
      nodes: [
        {
          id: "feature-beta",
          name: "Billing",
          description: "Handles payments",
          status: "in-progress",
          metadata: { priority: "high" },
        },
        { source: "./nodes/feature-alpha.yaml" },
      ],
      edges: [
        { manifest: "./edges/common.yaml" },
        {
          source: "feature-alpha",
          target: "feature-beta",
          type: "depends-on",
          metadata: { weight: "major" },
        },
      ],
    };

    const yaml = renderFeatureGraphYaml(graph);
    const expected = [
      "version: 2",
      "nodes:",
      "  - id: feature-beta",
      "    name: Billing",
      "    description: Handles payments",
      "    status: in-progress",
      "    metadata:",
      "      priority: high",
      "  - source: ./nodes/feature-alpha.yaml",
      "edges:",
      "  - manifest: ./edges/common.yaml",
      "  - source: feature-alpha",
      "    target: feature-beta",
      "    type: depends-on",
      "    metadata:",
      "      weight: major",
      "artifacts:",
      "  - apps/web/src/auth/__tests__/login.test.ts",
      "  - metadata:",
      "      suite: api",
      "    path: apps/api/tests/auth.spec.ts",
      "  - description: Spec",
      "    path: docs/features/spec.md",
      "",
    ].join("\n");
    expect(yaml).toBe(expected);
  });
});
