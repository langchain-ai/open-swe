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

export function parseGitHubRemote(remote: string): GitHubRepo | null {
  const parts = splitRemote(remote.trim())
  if (parts === null || !GITHUB_HOSTS.has(parts.host.toLowerCase())) return null
  const segments = parts.path.replace(/^\/+/, "").replace(/\/+$/, "").split("/")
  if (segments.length !== 2) return null
  const owner = segments[0] ?? ""
  const name = (segments[1] ?? "").replace(/\.git$/i, "")
  if (!owner || !name) return null
  return { owner, name }
}

export function repoFullName(repo: GitHubRepo): string {
  return `${repo.owner}/${repo.name}`
}

async function git(
  cwd: string,
  args: readonly string[]
): Promise<string | null> {
  const proc = Bun.spawn(["git", ...args], {
    cwd,
    stdout: "pipe",
    stderr: "ignore",
  })
  const output = await new Response(proc.stdout).text()
  const code = await proc.exited
  return code === 0 ? output.trim() : null
}

export async function isGitRepository(cwd: string): Promise<boolean> {
  return (await git(cwd, ["rev-parse", "--git-dir"])) !== null
}

export async function originRepo(cwd: string): Promise<GitHubRepo | null> {
  const remote = await git(cwd, ["remote", "get-url", "origin"])
  return remote === null ? null : parseGitHubRemote(remote)
}
