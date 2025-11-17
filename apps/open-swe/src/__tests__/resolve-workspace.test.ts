import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { resolveWorkspace } from "../graphs/manager/nodes/resolve-workspace.js";
import { createGitWorkspace } from "./helpers/workspace.js";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import type { GraphConfig } from "@openswe/shared/open-swe/types";

describe("resolveWorkspace", () => {
  beforeAll(() => {
    process.env.OPEN_SWE_DISABLE_FEATURE_GRAPH_GENERATION = "true";
  });

  it("uses the default feature graph when the workspace graph is missing", async () => {
    const { workspacePath, config, cleanup } = await createGitWorkspace();

    try {
      const state = {
        messages: [],
        internalMessages: [],
        workspaceAbsPath: workspacePath,
        targetRepository: { owner: "acme", repo: "demo" },
        taskPlan: { tasks: [], activeTaskIndex: 0 },
        branchName: "main",
      } as unknown as ManagerGraphState;

      const updates = await resolveWorkspace(state, config as GraphConfig);

      expect(updates.workspacePath).toBe(workspacePath);
      expect(updates.featureGraph?.hasFeature("sample-feature")).toBe(true);
    } finally {
      await cleanup();
    }
  });

  it("generates a feature graph when no graph exists", async () => {
    const { workspacePath, config, cleanup } = await createGitWorkspace({
      initializeGraph: false,
    });

    try {
      const graphDir = path.join(workspacePath, "features", "graph");
      await mkdir(graphDir, { recursive: true });
      const graphPath = path.join(graphDir, "graph.yaml");
      await writeFile(
        graphPath,
        `version: 1\nnodes:\n  - id: auto-feature\n    name: Auto generated\n    description: Created by test\n    status: active\n    development_progress: Completed\nedges: []\n`,
        "utf8",
      );

      const state = {
        messages: [],
        internalMessages: [],
        workspaceAbsPath: workspacePath,
        targetRepository: { owner: "acme", repo: "demo" },
        taskPlan: { tasks: [], activeTaskIndex: 0 },
        branchName: "main",
      } as unknown as ManagerGraphState;

      const updates = await resolveWorkspace(state, config as GraphConfig);

      expect(updates.featureGraph?.hasFeature("auto-feature")).toBe(true);
      expect(updates.activeFeatureIds).toEqual(["auto-feature"]);
    } finally {
      await cleanup();
    }
  });
});
