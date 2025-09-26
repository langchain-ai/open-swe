import { describe, it, expect, beforeEach, afterEach } from "@jest/globals";
import path from "node:path";
import { promises as fs } from "node:fs";
import {
  handleCreateCommand,
  handleViewCommand,
  handleStrReplaceCommand,
  handleInsertCommand,
} from "../builtin-tools/handlers.js";
import type { WorkspaceTestContext } from "../../__tests__/helpers/workspace.js";
import { createGitWorkspace, getLastCommitMessage } from "../../__tests__/helpers/workspace.js";
import type { Sandbox } from "../../utils/sandbox.js";

const sandbox = null as unknown as Sandbox;

describe("workspace text editing handlers", () => {
  let context: WorkspaceTestContext;

  beforeEach(async () => {
    context = await createGitWorkspace();
  });

  afterEach(async () => {
    await context.cleanup();
  });

  it("creates files via workspace fs and commits", async () => {
    const message = await handleCreateCommand(sandbox, context.config, {
      path: "docs/new-file.txt",
      workDir: ".",
      fileText: "initial content",
    });

    expect(message).toBe("Successfully created file docs/new-file.txt.");

    const created = await fs.readFile(
      path.join(context.workspacePath, "docs/new-file.txt"),
      "utf-8",
    );
    expect(created).toBe("initial content");

    expect(getLastCommitMessage(context.workspacePath)).toMatch(
      /^OpenSWE auto-commit #1/,
    );
  });

  it("replaces and inserts content in workspace files", async () => {
    const filePath = path.join(context.workspacePath, "src/file.ts");
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, "const value = 1;\n", "utf-8");

    const replaceMessage = await handleStrReplaceCommand(
      sandbox,
      context.config,
      {
        path: "src/file.ts",
        workDir: ".",
        oldStr: "1",
        newStr: "2",
      },
    );

    expect(replaceMessage).toBe(
      "Successfully replaced text in src/file.ts at exactly one location.",
    );

    const insertMessage = await handleInsertCommand(sandbox, context.config, {
      path: "src/file.ts",
      workDir: ".",
      insertLine: 1,
      newStr: "console.log(value);",
    });

    expect(insertMessage).toBe(
      "Successfully inserted text in src/file.ts at line 1.",
    );

    const updated = await fs.readFile(filePath, "utf-8");
    expect(updated).toBe("const value = 2;\nconsole.log(value);\n");

    expect(getLastCommitMessage(context.workspacePath)).toMatch(
      /^OpenSWE auto-commit #2/,
    );
  });

  it("views workspace directories with line numbers", async () => {
    const subdir = path.join(context.workspacePath, "src");
    await fs.mkdir(subdir, { recursive: true });
    await fs.writeFile(path.join(subdir, "index.ts"), "line1\nline2", "utf-8");

    const directoryListing = await handleViewCommand(sandbox, context.config, {
      path: ".",
      workDir: ".",
    });

    expect(directoryListing).toContain("Directory listing for .");
    expect(directoryListing).toContain("d src");

    const fileView = await handleViewCommand(sandbox, context.config, {
      path: "src/index.ts",
      workDir: ".",
      viewRange: [2, -1],
    });

    expect(fileView).toBe("2: line2");
  });
});
