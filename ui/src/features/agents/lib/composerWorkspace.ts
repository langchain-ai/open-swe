import type { WorkspaceOption } from "@/lib/api"

export interface ComposerWorkspaceInputs {
  /** The user's explicit pick in this composer, if any. */
  override: string | null
  /** The workspace owning the selected repository, if any. */
  repoWorkspace: string | null
  /** The signed-in user's saved default workspace. */
  userDefault: string | null | undefined
  /** The instance default reported by the backend. */
  instanceDefault: string | null
  workspaces: ReadonlyArray<Pick<WorkspaceOption, "slug">>
}

/**
 * The workspace a new thread is composed in, mirroring the backend's routing:
 * an explicit pick, else the repository's owner, else the user's default, else
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

/**
 * The repositories a thread composed in `slug` may work in: the ones the
 * workspace owns plus its default repository, and for the default workspace
 * every accessible repository no other workspace claims. With no matching
 * workspace, everything accessible is offered.
 */
export function reposForWorkspace(
  slug: string | null,
  workspaces: ReadonlyArray<
    Pick<WorkspaceOption, "slug" | "repos" | "is_default" | "default_repo">
  >,
  accessible: ReadonlyArray<RepoOption>
): Array<RepoOption> {
  const workspace = workspaces.find((candidate) => candidate.slug === slug)
  if (!workspace) return [...accessible]
  const lower = (name: string) => name.toLowerCase()
  const claimed = new Set(workspaces.flatMap((w) => w.repos.map(lower)))
  const own = new Set(
    [...workspace.repos, workspace.default_repo ?? ""].map(lower)
  )
  return accessible.filter(
    ({ full_name }) =>
      own.has(lower(full_name)) ||
      (workspace.is_default && !claimed.has(lower(full_name)))
  )
}

export interface ComposerRepoInputs {
  /** The user's pick in this composer: a repository, `null` for none, `undefined` for untouched. */
  override: string | null | undefined
  /** The signed-in user's saved default repository. */
  userDefault: string | null
  recentRepo?: string | null
  /** The selected workspace's effective default repository. */
  workspaceDefault: string | null
  /** What the selected workspace may work in; see {@link reposForWorkspace}. */
  offered: ReadonlyArray<RepoOption>
}

/** Resolve explicit, recent, and configured selections within the offered repositories. */
export function pickComposerRepo({
  override,
  recentRepo,
  userDefault,
  workspaceDefault,
  offered,
}: ComposerRepoInputs): string | null {
  if (override !== undefined) return override
  const wanted = [recentRepo, userDefault, workspaceDefault].map((c) =>
    c?.toLowerCase()
  )
  for (const name of wanted) {
    const match =
      name && offered.find((r) => r.full_name.toLowerCase() === name)
    if (match) return match.full_name
  }
  return null
}
