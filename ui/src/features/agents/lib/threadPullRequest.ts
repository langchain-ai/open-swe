import type { AgentThread, ReviewPageRef } from "@/features/agents/lib/types"

export function parseGitHubPullRequestUrl(
  href: string | undefined
): { repoFullName: string; number: number } | null {
  if (!href) return null
  try {
    const url = new URL(href)
    if (url.hostname !== "github.com" && url.hostname !== "www.github.com") {
      return null
    }
    const match = /^\/([^/]+)\/([^/]+)\/pull\/(\d+)\/?$/.exec(url.pathname)
    if (!match) return null
    return {
      repoFullName: `${match[1]}/${match[2]}`,
      number: Number(match[3]),
    }
  } catch {
    return null
  }
}

/** The pull request a thread opened most recently, as GitHub and our review page address it. */
export function threadPullRequest(
  thread: Pick<AgentThread, "pr" | "pullRequests">
): { url: string; review: ReviewPageRef } | null {
  const url = thread.pullRequests?.at(-1)?.url ?? thread.pr?.url
  const parsed = parseGitHubPullRequestUrl(url)
  if (!url || !parsed) return null
  const [owner = "", repo = ""] = parsed.repoFullName.split("/")
  return { url, review: { owner, repo, number: parsed.number } }
}
