import { jest } from "@jest/globals";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

describe("planner feature graph helpers", () => {
  beforeEach(() => {
    jest.resetModules();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  const createWorkspaceWithGraph = async () => {
    const workspacePath = await mkdtemp(path.join(tmpdir(), "planner-graph-"));
    const graphDir = path.join(workspacePath, "features", "graph");
    await mkdir(graphDir, { recursive: true });

    const primaryNodePath = path.join(graphDir, "feature-primary.yaml");
    const dependencyNodePath = path.join(graphDir, "feature-supporting.yaml");

    await writeFile(
      primaryNodePath,
      [
        "id: feature-primary",
        "name: Primary feature",
        "description: Implements the primary capability",
        "status: active",
        "",
      ].join("\n"),
      "utf8",
    );

    await writeFile(
      dependencyNodePath,
      [
        "id: feature-supporting",
        "name: Supporting feature",
        "description: Enables the primary capability",
        "status: planned",
        "",
      ].join("\n"),
      "utf8",
    );

    const graphYaml = [
      "version: 1",
      "nodes:",
      `  - source: ${primaryNodePath}`,
      `  - source: ${dependencyNodePath}`,
      "edges:",
      "  - source: feature-supporting",
      "    target: feature-primary",
      "    type: upstream",
      "",
    ].join("\n");

    const graphPath = path.join(graphDir, "graph.yaml");
    await writeFile(graphPath, graphYaml, "utf8");

    return {
      workspacePath,
      graphPath,
      cleanup: () => rm(workspacePath, { recursive: true, force: true }),
    };
  };

  const createWorkspaceWithoutGraph = async () => {
    const workspacePath = await mkdtemp(path.join(tmpdir(), "planner-missing-"));
    return {
      workspacePath,
      cleanup: () => rm(workspacePath, { recursive: true, force: true }),
    };
  };

  it("loads active features and dependencies once the graph is cached", async () => {
    const { workspacePath, graphPath, cleanup } = await createWorkspaceWithGraph();
    const { resolveActiveFeatures, resolveFeatureDependencies } = await import(
      "../utils/feature-graph.js"
    );

    const activeFeatures = await resolveActiveFeatures({
      workspacePath,
      featureIds: ["feature-primary", "feature-supporting"],
    });

    const dependencies = await resolveFeatureDependencies({
      workspacePath,
      featureIds: ["feature-primary"],
    });

    expect(activeFeatures.map((feature) => feature.id)).toEqual([
      "feature-primary",
      "feature-supporting",
    ]);

    await rm(graphPath);

    expect(dependencies.map((feature) => feature.id)).toEqual([
      "feature-supporting",
    ]);

    await cleanup();
  });

  it("warns and returns empty results when the graph file is missing", async () => {
    const { workspacePath, cleanup } = await createWorkspaceWithoutGraph();
    const consoleSpy = jest.spyOn(console, "log").mockImplementation(() => {});
    const { resolveActiveFeatures, resolveFeatureDependencies } = await import(
      "../utils/feature-graph.js"
    );

    const activeFeatures = await resolveActiveFeatures({
      workspacePath,
      featureIds: ["feature-primary"],
    });
    const dependencies = await resolveFeatureDependencies({
      workspacePath,
      featureIds: ["feature-primary"],
    });

    expect(activeFeatures).toEqual([]);
    expect(dependencies).toEqual([]);
    expect(
      consoleSpy.mock.calls.some(([message]) =>
        typeof message === "string" && message.includes("Unable to load feature graph"),
      ),
    ).toBe(true);

    await cleanup();
  });
});
