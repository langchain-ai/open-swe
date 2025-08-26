import { DAYTONA_SNAPSHOT_NAME } from "@open-swe/shared/constants";
import { CreateSandboxFromSnapshotParams } from "@daytonaio/sdk";

export const DEFAULT_SANDBOX_CREATE_PARAMS: CreateSandboxFromSnapshotParams = {
  user: "daytona",
  snapshot: DAYTONA_SNAPSHOT_NAME,
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

export enum RequestSource {
  GITHUB_ISSUE_WEBHOOK = "github_issue_webhook",
}
