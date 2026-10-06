/** Which GitHub repository a checkout's `origin` remote names. */

export interface GitHubRepo {
  owner: string
  name: string
}

const GITHUB_HOSTS = new Set(["github.com", "www.github.com"])
const SCHEME = /^[a-z][a-z0-9+.-]*:\/\//i
const SCP_LIKE = /^(?:[^@/]+@)?([^:/]+):(.+)$/

function splitRemote(remote: string): { host: string; path: string } | null {
  if (SCHEME.test(remote)) {
    try {
      const url = new URL(remote)
      return { host: url.hostname, path: url.pathname }
    } catch {
      return null
    }
  }
  const scp = SCP_LIKE.exec(remote)
  if (scp === null) return null
  const [, host, path] = scp
  if (host === undefined || path === undefined) return null
  return { host, path }
}

/** A loop, not `/\/+$/`: a regex anchored that way backtracks on runs of slashes. */
function trimSlashes(path: string): string {
  let start = 0
  let end = path.length
  while (start < end && path[start] === "/") start += 1
  while (end > start && path[end - 1] === "/") end -= 1
  return path.slice(start, end)
}

export function parseGitHubRemote(remote: string): GitHubRepo | null {
  const parts = splitRemote(remote.trim())
  if (parts === null || !GITHUB_HOSTS.has(parts.host.toLowerCase())) return null
  const segments = trimSlashes(parts.path).split("/")
  if (segments.length !== 2) return null
  const owner = segments[0] ?? ""
  const name = (segments[1] ?? "").replace(/\.git$/i, "")
  if (!owner || !name) return null
  return { owner, name }
}

export function repoFullName(repo: GitHubRepo): string {
  return `${repo.owner}/${repo.name}`
}
