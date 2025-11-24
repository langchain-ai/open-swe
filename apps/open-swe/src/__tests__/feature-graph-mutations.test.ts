import path from "node:path";
import { beforeEach, describe, expect, it, jest } from "@jest/globals";
import { FeatureGraph } from "@openswe/shared/feature-graph";
import { FEATURE_GRAPH_RELATIVE_PATH } from "../graphs/manager/utils/feature-graph-path.js";

const mkdirSpy = jest.fn();
const writeGraphSpy = jest.fn();
let persistFeatureGraph: typeof import("../graphs/manager/utils/feature-graph-mutations.js")["persistFeatureGraph"];

describe("persistFeatureGraph", () => {
  const workspacePath = "/tmp/workspace";
  const graphPath = path.join(workspacePath, FEATURE_GRAPH_RELATIVE_PATH);

  beforeAll(async () => {
    await jest.unstable_mockModule("node:fs/promises", () => ({
      __esModule: true,
      default: { mkdir: mkdirSpy },
      mkdir: mkdirSpy,
    }));
    await jest.unstable_mockModule("@openswe/shared/feature-graph/writer", () => ({
      __esModule: true,
      writeFeatureGraphFile: writeGraphSpy,
    }));

    ({ persistFeatureGraph } = await import(
      "../graphs/manager/utils/feature-graph-mutations.js"
    ));
  });

  beforeEach(() => {
    mkdirSpy.mockReset();
    writeGraphSpy.mockReset();
  });

  it("persists validated graphs to the workspace graph file", async () => {
    const graph = new FeatureGraph({
      version: 1,
      nodes: new Map([
        [
          "feature-auth",
          {
            id: "feature-auth",
            name: "Auth",
            description: "Authentication",
            status: "inactive",
          },
        ],
      ]),
      edges: [],
    });

    await persistFeatureGraph(graph, workspacePath);

    expect(writeGraphSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        graphPath,
        version: 1,
        nodes: [expect.objectContaining({ id: "feature-auth" })],
        edges: [],
      }),
    );
  });

  it("rejects graphs with edges that target missing features", async () => {
    const graph = new FeatureGraph({
      version: 1,
      nodes: new Map([
        [
          "feature-auth",
          {
            id: "feature-auth",
            name: "Auth",
            description: "Authentication",
            status: "inactive",
          },
        ],
      ]),
      edges: [
        {
          source: "feature-auth",
          target: "missing-feature",
          type: "depends-on",
        },
      ],
    });

    await expect(persistFeatureGraph(graph, workspacePath)).rejects.toThrow(
      "Feature edge references unknown feature: feature-auth -> missing-feature (depends-on)",
    );
    expect(writeGraphSpy).not.toHaveBeenCalled();
  });

  it("rejects graphs with duplicate edges", async () => {
    const graph = new FeatureGraph({
      version: 1,
      nodes: new Map([
        [
          "feature-auth",
          {
            id: "feature-auth",
            name: "Auth",
            description: "Authentication",
            status: "inactive",
          },
        ],
        [
          "feature-billing",
          {
            id: "feature-billing",
            name: "Billing",
            description: "Billing",
            status: "inactive",
          },
        ],
      ]),
      edges: [
        {
          source: "feature-auth",
          target: "feature-billing",
          type: "depends-on",
        },
        {
          source: "feature-auth",
          target: "feature-billing",
          type: "depends-on",
        },
      ],
    });

    await expect(persistFeatureGraph(graph, workspacePath)).rejects.toThrow(
      "Duplicate feature edge detected: feature-auth->feature-billing#depends-on",
    );
    expect(writeGraphSpy).not.toHaveBeenCalled();
  });
});
