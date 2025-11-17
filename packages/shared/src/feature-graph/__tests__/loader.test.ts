import { mkdtemp, writeFile, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { loadFeatureGraph } from "../loader.js";

const createTempDir = async () =>
  mkdtemp(path.join(tmpdir(), "feature-graph-loader-"));

describe("loadFeatureGraph", () => {
  it("loads nodes, edges, and artifacts from mixed sources", async () => {
    const workspace = await createTempDir();
    try {
      const graphDir = path.join(workspace, "features", "graph");
      await mkdir(path.join(graphDir, "nodes"), { recursive: true });
      await mkdir(path.join(graphDir, "edges"), { recursive: true });

      await writeFile(
        path.join(graphDir, "nodes", "auth.yaml"),
        `id: feature-auth\nname: Authentication\ndescription: Handles login\nstatus: active\ndevelopment_progress: In Progress\nartifacts:\n  - apps/web/src/auth/__tests__/login.test.ts\n`,
        "utf8",
      );

      await writeFile(
        path.join(graphDir, "nodes", "common.yaml"),
        `sources:\n  - manifest: ./shared.yaml\n`,
        "utf8",
      );

      await writeFile(
        path.join(graphDir, "nodes", "shared.yaml"),
        `sources:\n  - ./audit.yaml\n`,
        "utf8",
      );

      await writeFile(
        path.join(graphDir, "nodes", "audit.yaml"),
        `id: feature-audit\nname: Audit logging\ndescription: Tracks security events\nstatus: proposed\ndevelopment_progress: To Do\n`,
        "utf8",
      );

      await writeFile(
        path.join(graphDir, "edges", "primary.yaml"),
        `source: feature-auth\ntarget: feature-audit\ntype: depends-on\nmetadata:\n  confidence: high\n`,
        "utf8",
      );

      await writeFile(
        path.join(graphDir, "edges", "common.yaml"),
        `sources:\n  - manifest: ./support.yaml\n`,
        "utf8",
      );

      await writeFile(
        path.join(graphDir, "edges", "support.yaml"),
        `sources:\n  - ./handoff.yaml\n`,
        "utf8",
      );

      await writeFile(
        path.join(graphDir, "edges", "handoff.yaml"),
        `source: feature-audit\ntarget: feature-inline\ntype: relates-to\n`,
        "utf8",
      );

      await writeFile(
        path.join(graphDir, "graph.yaml"),
        `version: 1\nnodes:\n  - source: ./nodes/auth.yaml\n  - manifest: ./nodes/common.yaml\n  - id: feature-inline\n    name: Inline feature\n    description: Ad-hoc definition\n    status: in-progress\nedges:\n  - source: ./edges/primary.yaml\n  - manifest: ./edges/common.yaml\n  - source: feature-auth\n    target: feature-inline\n    type: supports\nartifacts:\n  docs:\n    path: docs/features/overview.md\n`,
        "utf8",
      );

      const data = await loadFeatureGraph(path.join(graphDir, "graph.yaml"));

      expect(data.version).toBe(1);
      expect(Array.from(data.nodes.keys()).sort()).toEqual([
        "feature-audit",
        "feature-auth",
        "feature-inline",
      ]);

      const inlineFeature = data.nodes.get("feature-inline");
      expect(inlineFeature).toEqual(
        expect.objectContaining({
          id: "feature-inline",
          name: "Inline feature",
          description: "Ad-hoc definition",
          status: "in-progress",
        }),
      );

      expect(inlineFeature?.development_progress).toBeUndefined();

      expect(data.edges).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            source: "feature-auth",
            target: "feature-inline",
            type: "supports",
          }),
          expect.objectContaining({
            source: "feature-auth",
            target: "feature-audit",
            metadata: { confidence: "high" },
          }),
          expect.objectContaining({
            source: "feature-audit",
            target: "feature-inline",
          }),
        ]),
      );

      expect(data.artifacts).toEqual({
        docs: { path: "docs/features/overview.md" },
      });
    } finally {
      await rm(workspace, { recursive: true, force: true });
    }
  });

  it("rejects duplicate feature identifiers", async () => {
    const workspace = await createTempDir();
    try {
      const graphDir = path.join(workspace, "features", "graph");
      await mkdir(graphDir, { recursive: true });

      await writeFile(
        path.join(graphDir, "graph.yaml"),
        `version: 1\nnodes:\n  - id: feature-dup\n    name: Duplicate\n    description: First\n    status: active\n  - id: feature-dup\n    name: Duplicate again\n    description: Second\n    status: active\nedges: []\n`,
        "utf8",
      );

      await expect(
        loadFeatureGraph(path.join(graphDir, "graph.yaml")),
      ).rejects.toThrow(/duplicate feature node id/i);
    } finally {
      await rm(workspace, { recursive: true, force: true });
    }
  });

  it("detects circular manifest references", async () => {
    const workspace = await createTempDir();
    try {
      const graphDir = path.join(workspace, "features", "graph");
      await mkdir(path.join(graphDir, "nodes"), { recursive: true });

      await writeFile(
        path.join(graphDir, "nodes", "loop.yaml"),
        `sources:\n  - manifest: ./loop.yaml\n`,
        "utf8",
      );

      await writeFile(
        path.join(graphDir, "graph.yaml"),
        `version: 1\nnodes:\n  - manifest: ./nodes/loop.yaml\nedges: []\n`,
        "utf8",
      );

      await expect(
        loadFeatureGraph(path.join(graphDir, "graph.yaml")),
      ).rejects.toThrow(/circular feature node manifest/i);
    } finally {
      await rm(workspace, { recursive: true, force: true });
    }
  });
});
