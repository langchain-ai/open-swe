import { SANDBOX_ROOT_DIR } from "./constants.js";
import { TargetRepository, GraphConfig } from "./open-swe/types.js";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "./open-swe/local-mode.js";

/**
 * Strips invalid git branch characters from a string.
 */
export function sanitizeBranchName(name: string): string {
  let sanitized = name
    .trim()
    .replace(/[\s~^:?*[\]\\]+/g, "-")
    .replace(/\.{2,}/g, "-")
    .replace(/\/{2,}/g, "/")
    .replace(/\.lock(\/|$)/g, "-lock$1")
    .replace(/^[.\-/]+/, "")
    .replace(/[.\-/]+$/, "")
    .replace(/-{2,}/g, "-");

  if (!sanitized) {
    sanitized = "branch";
  }

  return sanitized;
}

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
