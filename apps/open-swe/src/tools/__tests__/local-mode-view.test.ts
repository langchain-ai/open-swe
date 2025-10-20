import { describe, it, beforeEach, afterEach, expect } from "@jest/globals";
import path from "node:path";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import type { GraphConfig, GraphState } from "@openswe/shared/open-swe/types";
import { createViewTool } from "../builtin-tools/view.js";
import { createTextEditorTool } from "../builtin-tools/text-editor.js";

function createLocalGraphConfig(): GraphConfig {
  return {
    configurable: { "x-local-mode": "true" },
    thread_id: "thread-id",
    assistant_id: "assistant-id",
    callbacks: [],
    metadata: {},
    tags: [],
  } as unknown as GraphConfig;
}

describe("local mode path normalization for view operations", () => {
  let repoRoot: string;
  let previousLocalPath: string | undefined;
  let config: GraphConfig;
  let state: Pick<GraphState, "sandboxSessionId" | "targetRepository">;

  beforeEach(async () => {
    repoRoot = await mkdtemp(path.join(tmpdir(), "open-swe-local-"));
    await mkdir(path.join(repoRoot, "docs"), { recursive: true });
    await writeFile(
      path.join(repoRoot, "docs", "notes.txt"),
      "first line\nsecond line\nthird line",
      "utf-8",
    );

    previousLocalPath = process.env.OPEN_SWE_LOCAL_PROJECT_PATH;
    process.env.OPEN_SWE_LOCAL_PROJECT_PATH = repoRoot;

    config = createLocalGraphConfig();
    state = {
      sandboxSessionId: "",
      targetRepository: { owner: "test", repo: "project" },
    };
  });

  afterEach(async () => {
    await rm(repoRoot, { recursive: true, force: true });
    if (previousLocalPath === undefined) {
      delete process.env.OPEN_SWE_LOCAL_PROJECT_PATH;
    } else {
      process.env.OPEN_SWE_LOCAL_PROJECT_PATH = previousLocalPath;
    }
  });

  it("reads files with line ranges using project prefixed paths", async () => {
    const viewTool = createViewTool(state, config);

    const response = await viewTool.invoke({
      command: "view",
      path: "project/docs/notes.txt",
      view_range: [2, -1],
    });

    expect(response.status).toBe("success");
    expect(response.result).toBe("2: second line\n3: third line");
  });

  it("lists directories for sandbox absolute paths", async () => {
    const textEditorTool = createTextEditorTool(state, config);

    const response = await textEditorTool.invoke({
      command: "view",
      path: "/sandbox/project/docs",
    });

    expect(response.status).toBe("success");
    expect(response.result).toContain(
      "Directory listing for /sandbox/project/docs:",
    );
    expect(response.result).toContain("- notes.txt");
  });
});
