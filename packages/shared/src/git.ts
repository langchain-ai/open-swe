import { SANDBOX_ROOT_DIR } from "./constants.js";
import { TargetRepository, GraphConfig } from "./open-swe/types.js";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "./open-swe/local-mode.js";

/**
 * Strips invalid git branch characters from a string and returns a valid branch name.
 */
// eslint-disable-next-line no-control-regex
const INVALID_BRANCH_CHARS = /[\x00-\x1f\x7f~^:?*[\]{}\\]/g;

export function sanitizeBranchName(name: string): string {
  let sanitized = name
    .replace(/@\{/g, "--")
    .replace(INVALID_BRANCH_CHARS, "-")
    .replace(/\s+/g, "-")
    .replace(/\.{2,}/g, "-")
    .replace(/\/\//g, "/")
    .replace(/\.lock(\/|$)/g, "-lock$1");

  sanitized = sanitized.replace(/^[./]+/, "").replace(/[./]+$/, "");

  sanitized = sanitized.replace(/-{2,}/g, "-").replace(/-$/g, "");

  if (!sanitized) {
    return "branch";
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
