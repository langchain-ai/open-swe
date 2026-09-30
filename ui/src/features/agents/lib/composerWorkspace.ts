import type { WorkspaceOption } from "@/lib/api"

export interface ComposerWorkspaceInputs {
  /** The user's explicit pick in this composer, if any. */
  override: string | null
  /** The workspace preferring the selected repository, if any. */
  repoWorkspace: string | null
  /** The signed-in user's saved default workspace. */
  userDefault: string | null | undefined
  /** The instance default reported by the backend. */
  instanceDefault: string | null
  workspaces: ReadonlyArray<Pick<WorkspaceOption, "slug">>
}

/**
 * The workspace a new thread is composed in, mirroring the backend's routing:
 * an explicit pick, else the repository's preferred workspace, else the user's
 * default, else
 * the instance default. A default only counts when it names a listed
 * workspace, so a deleted one falls through instead of being sent to the run.
 */
export function pickComposerWorkspace({
  override,
  repoWorkspace,
  userDefault,
  instanceDefault,
  workspaces,
}: ComposerWorkspaceInputs): string | null {
  if (override) return override
  if (repoWorkspace) return repoWorkspace
  const known = new Set(workspaces.map((workspace) => workspace.slug))
  for (const slug of [userDefault, instanceDefault]) {
    if (slug && known.has(slug)) return slug
  }
  return null
}

type RepoOption = { full_name: string }

export interface ComposerRepoInputs {
  /** The user's pick in this composer: a repository, `null` for none, `undefined` for untouched. */
  override: string | null | undefined
  /** The signed-in user's saved default repository. */
  userDefault: string | null
  /** The selected workspace's effective default repository. */
  workspaceDefault: string | null
  /** The repositories the composer offers: every one the GitHub App can reach. */
  offered: ReadonlyArray<RepoOption>
}

/**
 * The repository a new thread starts in once the workspace is settled: the
 * explicit pick, else the user's default when the workspace may use it, else
 * the workspace's own default, else none.
 */
export function pickComposerRepo({
  override,
  userDefault,
  workspaceDefault,
  offered,
}: ComposerRepoInputs): string | null {
  if (override !== undefined) return override
  const wanted = [userDefault, workspaceDefault].map((c) => c?.toLowerCase())
  for (const name of wanted) {
    const match =
      name && offered.find((r) => r.full_name.toLowerCase() === name)
    if (match) return match.full_name
  }
  return null
}
