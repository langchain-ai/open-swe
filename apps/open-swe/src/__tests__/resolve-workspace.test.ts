import { resolveWorkspace } from "../graphs/manager/nodes/resolve-workspace.js";
import { createGitWorkspace } from "./helpers/workspace.js";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import type { GraphConfig } from "@openswe/shared/open-swe/types";

describe("resolveWorkspace", () => {
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
});
