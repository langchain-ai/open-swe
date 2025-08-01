import { SANDBOX_ROOT_DIR } from "./constants.js";
import { TargetRepository, GraphConfig } from "./open-swe/types.js";

// Local mode utility function (duplicated here since shared package doesn't have access to local-mode)
function isLocalMode(config?: GraphConfig): boolean {
  return (config?.configurable as any)?.["x-local-mode"] === "true";
}

function getLocalWorkingDirectory(): string {
  return process.cwd();
}

export function getRepoAbsolutePath(
  targetRepository: TargetRepository,
  config?: GraphConfig,
): string {
  // Check for local mode first
  if (isLocalMode(config)) {
    return getLocalWorkingDirectory();
  }

  const repoName = targetRepository.repo;
  if (!repoName) {
    throw new Error("No repository name provided");
  }

  return `${SANDBOX_ROOT_DIR}/${repoName}`;
}
