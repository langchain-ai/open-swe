import { SNAPSHOT_NAME } from "@open-swe/shared/constants";
import { CreateSandboxFromImageParams } from "@daytonaio/sdk";

export const DEFAULT_SANDBOX_CREATE_PARAMS: CreateSandboxFromImageParams = {
  resources: {
    cpu: 2,
    memory: 4,
    disk: 5,
  },
  user: "daytona",
  image: SNAPSHOT_NAME,
  autoDeleteInterval: 15, // delete after 15 minutes
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
