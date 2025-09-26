import { resolve } from "path";
import { SANDBOX_ROOT_DIR } from "./constants.js";
import { TargetRepository, GraphConfig } from "./open-swe/types.js";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "./open-swe/local-mode.js";

export function getRepoAbsolutePath(
  targetRepository: TargetRepository,
  config?: GraphConfig,
): string {
  const workspacePath = (config?.configurable as Record<string, unknown> | undefined)?.workspacePath;
  if (typeof workspacePath === "string" && workspacePath.trim().length > 0) {
    return workspacePath;
  }
  // Check for local mode first
  if (config && isLocalMode(config)) {
    return getLocalWorkingDirectory();
  }

  const repoName = targetRepository.repo;
  if (!repoName) {
    throw new Error("No repository name provided");
  }

  const relativeRepo = repoName.replace(/^\/+/, "");
  return resolve(SANDBOX_ROOT_DIR, relativeRepo);
}
