import { describe, it, expect, beforeEach, afterEach } from "@jest/globals";
import path from "node:path";
import { promises as fs } from "node:fs";
import { writeFile, readFile } from "../read-write.js";
import type { WorkspaceTestContext } from "../../__tests__/helpers/workspace.js";
import { createGitWorkspace, getLastCommitMessage } from "../../__tests__/helpers/workspace.js";

const sandbox = null;

describe("workspace read/write utilities", () => {
  let context: WorkspaceTestContext;

  beforeEach(async () => {
    context = await createGitWorkspace();
  });

  afterEach(async () => {
    await context.cleanup();
  });

  it("writes files directly to the workspace and commits changes", async () => {
    const result = await writeFile({
      sandbox,
      filePath: "src/example.txt",
      content: "hello world",
      config: context.config,
    });

    expect(result.success).toBe(true);
    expect(result.output).toContain("Successfully wrote file");

    const fullPath = path.join(context.workspacePath, "src/example.txt");
    await expect(fs.readFile(fullPath, "utf-8")).resolves.toBe("hello world");

    expect(getLastCommitMessage(context.workspacePath)).toMatch(
      /^OpenSWE auto-commit #1/,
    );
  });

  it("creates missing files on read and returns empty content", async () => {
    const response = await readFile({
      sandbox,
      filePath: "notes/missing.txt",
      config: context.config,
    });

    expect(response.success).toBe(true);
    expect(response.output).toBe("");

    const createdPath = path.join(context.workspacePath, "notes/missing.txt");
    const stats = await fs.stat(createdPath);
    expect(stats.isFile()).toBe(true);

    expect(getLastCommitMessage(context.workspacePath)).toMatch(
      /^OpenSWE auto-commit #1/,
    );
  });
});
