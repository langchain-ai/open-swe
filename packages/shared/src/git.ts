import { SANDBOX_ROOT_DIR } from "./constants.js";
import { TargetRepository, GraphConfig } from "./agent-mojo/types.js";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "./agent-mojo/local-mode.js";

export function getRepoAbsolutePath(
  targetRepository: TargetRepository,
  config?: GraphConfig,
): string {
  // Check for local mode first
  if (config && isLocalMode(config)) {
    return getLocalWorkingDirectory();
  }

  const repoName = targetRepository.repo;
  if (!repoName) {
    throw new Error("No repository name provided");
  }

  return `${SANDBOX_ROOT_DIR}/${repoName}`;
}
