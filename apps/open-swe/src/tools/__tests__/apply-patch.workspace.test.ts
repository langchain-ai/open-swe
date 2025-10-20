import { describe, it, expect, beforeEach, afterEach } from "@jest/globals";
import path from "node:path";
import { promises as fs } from "node:fs";
import { createApplyPatchTool } from "../apply-patch.js";
import type { WorkspaceTestContext } from "../../__tests__/helpers/workspace.js";
import { createGitWorkspace, getLastCommitMessage } from "../../__tests__/helpers/workspace.js";
import { stageAndCommitWorkspaceChanges } from "../../utils/git.js";
import type { GraphState } from "@openswe/shared/open-swe/types";
import { SANDBOX_ROOT_DIR } from "@openswe/shared/constants";

function createState(workspacePath: string): GraphState {
  const repoRelative = path.relative(SANDBOX_ROOT_DIR, workspacePath) || ".";
  return {
    targetRepository: { owner: "acme", repo: repoRelative },
  } as unknown as GraphState;
}

describe("workspace apply patch tool", () => {
  let context: WorkspaceTestContext;

  beforeEach(async () => {
    context = await createGitWorkspace();
  });

  afterEach(async () => {
    await context.cleanup();
  });

  it("applies git patches inside the workspace and commits", async () => {
    const filePath = path.join(context.workspacePath, "app/file.txt");
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, "old line\n", "utf-8");
    await stageAndCommitWorkspaceChanges(context.workspacePath);

    const diff = [
      "--- a/app/file.txt",
      "+++ b/app/file.txt",
      "@@ -1 +1,2 @@",
      "-old line",
      "+new line",
      "+another line",
      "",
    ].join("\n");

    const tool = createApplyPatchTool(
      createState(context.workspacePath),
      context.config,
    );
    const result = await tool.invoke({ diff, file_path: "app/file.txt" });

    expect(result.status).toBe("success");
    expect(result.result).toContain("Successfully applied diff to `app/file.txt`");

    const updated = await fs.readFile(filePath, "utf-8");
    expect(updated).toBe("new line\nanother line\n");

    expect(getLastCommitMessage(context.workspacePath)).toMatch(
      /^OpenSWE auto-commit #2/,
    );
  });

  it("uses the configured workspace path when local project path is outside", async () => {
    const filePath = path.join(context.workspacePath, "app/outside.txt");
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, "before\n", "utf-8");
    await stageAndCommitWorkspaceChanges(context.workspacePath);

    const diff = [
      "--- a/app/outside.txt",
      "+++ b/app/outside.txt",
      "@@ -1 +1,2 @@",
      "-before",
      "+after",
      "+workspace",
      "",
    ].join("\n");

    const previousLocalProjectPath = process.env.OPEN_SWE_LOCAL_PROJECT_PATH;
    const outsideProjectPath = path.join(context.workspacePath, "..", "external-project");
    await fs.mkdir(outsideProjectPath, { recursive: true });
    process.env.OPEN_SWE_LOCAL_PROJECT_PATH = outsideProjectPath;

    try {
      const tool = createApplyPatchTool(
        createState(context.workspacePath),
        context.config,
      );
      const result = await tool.invoke({ diff, file_path: "app/outside.txt" });

      expect(result.status).toBe("success");
      expect(result.result).toContain("Successfully applied diff to `app/outside.txt`");

      const updated = await fs.readFile(filePath, "utf-8");
      expect(updated).toBe("after\nworkspace\n");

      expect(getLastCommitMessage(context.workspacePath)).toMatch(
        /^OpenSWE auto-commit #2/,
      );
    } finally {
      if (previousLocalProjectPath === undefined) {
        delete process.env.OPEN_SWE_LOCAL_PROJECT_PATH;
      } else {
        process.env.OPEN_SWE_LOCAL_PROJECT_PATH = previousLocalProjectPath;
      }
    }
  });
});
