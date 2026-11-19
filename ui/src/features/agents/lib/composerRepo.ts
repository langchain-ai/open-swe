type RepoOption = { full_name: string }

export interface ComposerRepoInputs {
  /** The user's pick in this composer: a repository, `null` for none, `undefined` for untouched. */
  override: string | null | undefined
  /** The signed-in user's saved default repository. */
  userDefault: string | null
  defaultRepo: string | null
  /** The repositories the composer offers: every one the GitHub App can reach. */
  offered: ReadonlyArray<RepoOption>
}

export function pickComposerRepo({
  override,
  userDefault,
  defaultRepo,
  offered,
}: ComposerRepoInputs): string | null {
  if (override !== undefined) return override
  const wanted = [userDefault, defaultRepo].map((c) => c?.toLowerCase())
  for (const name of wanted) {
    const match =
      name && offered.find((r) => r.full_name.toLowerCase() === name)
    if (match) return match.full_name
  }
  return null
}
