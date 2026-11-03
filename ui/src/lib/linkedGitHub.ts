import type { SessionUser } from "./api"

/** Whether the person can use coding features: a LangSmith sign-in needs a linked GitHub account. */
export function hasLinkedGitHub(
  user: Pick<SessionUser, "auth_provider" | "github_login">
): boolean {
  return user.auth_provider !== "langsmith" || !!user.github_login
}
