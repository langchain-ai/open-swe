import { displayName } from "@/features/reviews/lib/logins"
import type { PullRequestRef } from "@/features/reviews/lib/reviewKeys"

const GITHUB = "https://github.com"

/** Where things about a pull request live on GitHub. */
export const githubUrls = {
  repo: ({ owner, repo }: PullRequestRef) => `${GITHUB}/${owner}/${repo}`,
  pullRequest: (pr: PullRequestRef, suffix = "") =>
    `${githubUrls.repo(pr)}/pull/${pr.number}${suffix}`,
  branch: (pr: PullRequestRef, ref: string) =>
    `${githubUrls.repo(pr)}/tree/${ref}`,
  file: (pr: PullRequestRef, sha: string, path: string) =>
    `${githubUrls.repo(pr)}/blob/${sha}/${path}`,
  /** Apps have no user page; their bot account lives under /apps. */
  profile: (author: { login: string; bot?: boolean }) =>
    (author.bot ?? author.login.endsWith("[bot]"))
      ? `${GITHUB}/apps/${displayName(author)}`
      : `${GITHUB}/${author.login}`,
}
