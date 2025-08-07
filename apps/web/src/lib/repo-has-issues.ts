import { Repository } from "@/utils/github";

/**
 * Returns true if the repository has issues enabled
 * @param repo The repository to check
 * @returns True if the repository has issues enabled
 */
export function repoHasIssuesEnabled(repo: Repository): boolean {
  return repo.has_issues;
}
