import type { MouseEvent } from "react"

import { displayName } from "@/features/reviews/lib/logins"
import type { PullRequestRef } from "@/features/reviews/lib/reviewKeys"

const GITHUB = "https://github.com"

/** Where things about a pull request live on GitHub. */
export const githubUrls = {
  openModifiedLink(event: MouseEvent<HTMLElement>) {
    if (event.button !== 0 || (!event.metaKey && !event.ctrlKey)) return
    const link =
      event.target instanceof Element ? event.target.closest("a[href]") : null
    if (!(link instanceof HTMLAnchorElement)) return
    const url = new URL(link.href)
    if (url.origin !== window.location.origin) return
    const match =
      /^\/agents\/reviews\/([^/]+)\/([^/]+)\/(\d+)\/?$/.exec(url.pathname) ??
      /^\/([^/]+)\/([^/]+)\/pull\/(\d+)\/?$/.exec(url.pathname)
    if (!match) return
    event.preventDefault()
    event.stopPropagation()
    window.open(
      githubUrls.pullRequest({
        owner: match[1]!,
        repo: match[2]!,
        number: Number(match[3]),
      }),
      "_blank",
      "noopener,noreferrer"
    )
  },
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
