export interface PullRequestRef {
  owner: string
  repo: string
  number: number
}

const URL_PATTERN = /github\.com\/([\w.-]+)\/([\w.-]+)\/pulls?\/(\d+)/i
const REFERENCE_PATTERN = /([\w.-]+)\/([\w.-]+)#(\d+)/
const NUMBER_PATTERN = /^#?(\d+)$/

/**
 * The pull request someone typed or pasted: a GitHub link or `owner/repo#123`
 * anywhere in the text, or a bare number when exactly one repository is shared.
 */
export function parsePullRequestInput(
  text: string,
  shared: readonly string[]
): PullRequestRef | null {
  const trimmed = text.trim()
  const bare = NUMBER_PATTERN.exec(trimmed)
  if (bare) {
    const only = shared.length === 1 ? shared[0] : undefined
    return only ? numbered(splitRepository(only), bare[1]) : null
  }
  const match = URL_PATTERN.exec(trimmed) ?? REFERENCE_PATTERN.exec(trimmed)
  if (!match) return null
  const [, owner = "", repo = "", number] = match
  return numbered({ owner, repo }, number)
}

function numbered(
  repository: { owner: string; repo: string } | null,
  digits: string | undefined
): PullRequestRef | null {
  const number = Number(digits)
  return repository && Number.isSafeInteger(number) && number > 0
    ? { ...repository, number }
    : null
}

export function splitRepository(
  fullName: string
): { owner: string; repo: string } | null {
  const [owner, repo, ...rest] = fullName.split("/")
  return owner && repo && rest.length === 0 ? { owner, repo } : null
}

export const repositoryOf = ({ owner, repo }: PullRequestRef) =>
  `${owner}/${repo}`

export const pullRequestLabel = (pr: PullRequestRef) =>
  `${repositoryOf(pr)}#${pr.number}`

export const pullRequestUrl = (pr: PullRequestRef) =>
  `https://github.com/${repositoryOf(pr)}/pull/${pr.number}`
