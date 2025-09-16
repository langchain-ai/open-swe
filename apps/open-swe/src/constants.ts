import {
  getLocalWorkingDirectory,
  isLocalModeFromEnv,
} from "@openswe/shared/open-swe/local-mode";
import { mkdtempSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const CONTAINER_PROJECT_ROOT = "/workspace/project";

const DEFAULT_SANDBOX_PATH =
  process.env.OPEN_SWE_PROJECT_PATH ||
  (isLocalModeFromEnv()
    ? getLocalWorkingDirectory()
    : CONTAINER_PROJECT_ROOT) ||
  mkdtempSync(path.join(os.tmpdir(), "open-swe-"));

const DEFAULT_SANDBOX_IMAGE = "ghcr.io/langchain-ai/open-swe/sandbox:latest";

export const SANDBOX_DOCKER_IMAGE =
  process.env.OPEN_SWE_SANDBOX_IMAGE?.trim() || DEFAULT_SANDBOX_IMAGE;

export const DEFAULT_SANDBOX_CREATE_PARAMS = {
  user: "open-swe",
  autoDeleteInterval: 15,
  envVars: {
    SANDBOX_ROOT_DIR: DEFAULT_SANDBOX_PATH,
  },
};

export const LANGGRAPH_USER_PERMISSIONS = [
  "threads:create",
  "threads:create_run",
  "threads:read",
  "threads:delete",
  "threads:update",
  "threads:search",
  "assistants:create",
  "assistants:read",
  "assistants:delete",
  "assistants:update",
  "assistants:search",
  "deployments:read",
  "deployments:search",
  "store:access",
];
