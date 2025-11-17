import { mkdtemp, mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import { execSync } from "node:child_process";
import type { GraphConfig } from "@openswe/shared/open-swe/types";

export interface WorkspaceTestContext {
  workspacePath: string;
  config: GraphConfig;
  cleanup: () => Promise<void>;
}

type WorkspaceOptions = {
  initializeGraph?: boolean;
};

function createGraphConfig(workspacePath: string): GraphConfig {
  return {
    configurable: { workspacePath },
    thread_id: "thread-id",
    assistant_id: "assistant-id",
    callbacks: [],
    metadata: {},
    tags: [],
  } as unknown as GraphConfig;
}

export async function createGitWorkspace(
  options: WorkspaceOptions = {},
): Promise<WorkspaceTestContext> {
  const previousWorkspacesRoot = process.env.WORKSPACES_ROOT;
  const root = await mkdtemp(path.join(tmpdir(), "open-swe-ws-root-"));
  const workspacePath = path.join(root, "workspace");
  await mkdir(workspacePath, { recursive: true });
  execSync("git init", { cwd: workspacePath });

  if (options.initializeGraph) {
    execSync(
      "mkdir -p features/graph && printf 'version: 1\nnodes: []\nedges: []\n' > features/graph/graph.yaml",
      {
        cwd: workspacePath,
      },
    );
  }
  process.env.WORKSPACES_ROOT = root;
  process.env.SKIP_CI_UNTIL_LAST_COMMIT = "false";
  return {
    workspacePath,
    config: createGraphConfig(workspacePath),
    cleanup: async () => {
      await rm(root, { recursive: true, force: true });
      if (previousWorkspacesRoot === undefined) {
        delete process.env.WORKSPACES_ROOT;
      } else {
        process.env.WORKSPACES_ROOT = previousWorkspacesRoot;
      }
    },
  };
}

export function getLastCommitMessage(repoPath: string): string {
  try {
    return execSync("git log -1 --pretty=%B", {
      cwd: repoPath,
      encoding: "utf-8",
    }).trim();
  } catch {
    return "";
  }
}
