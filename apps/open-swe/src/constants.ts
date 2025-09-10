import { getLocalWorkingDirectory } from "@openswe/shared/open-swe/local-mode";
import { mkdtempSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const DEFAULT_SANDBOX_PATH =
  process.env.OPEN_SWE_PROJECT_PATH ||
  getLocalWorkingDirectory() ||
  mkdtempSync(path.join(os.tmpdir(), "open-swe-"));

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
