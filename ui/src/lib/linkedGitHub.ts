import type { SessionUser } from "./api"

/** Whether the person has linked the GitHub account required to use Open SWE. */
export function hasLinkedGitHub(
  user: Pick<SessionUser, "auth_provider" | "github_login">
): boolean {
  return user.auth_provider !== "langsmith" || !!user.github_login
}
