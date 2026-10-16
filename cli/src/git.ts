import { parseGitHubRemote, type GitHubRepo } from "open-swe-bridge-client"

export {
  parseGitHubRemote,
  repoFullName,
  type GitHubRepo,
} from "open-swe-bridge-client"

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
