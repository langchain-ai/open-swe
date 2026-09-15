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
