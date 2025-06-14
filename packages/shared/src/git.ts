import { SANDBOX_ROOT_DIR } from "./constants.js";
import { TargetRepository } from "./open-swe/types.js";

export function getRepoAbsolutePath(
  targetRepository: TargetRepository,
): string {
  const repoName = targetRepository.repo;
  if (!repoName) {
    throw new Error("No repository name provided");
  }

  return `${SANDBOX_ROOT_DIR}/${repoName}`;
}
