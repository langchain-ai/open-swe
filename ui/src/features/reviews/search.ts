export type ReviewSort = "updatedAt" | "createdAt"
export const reviewStatuses = [
  "Draft",
  "Conflicted",
  "Failing",
  "Pending",
  "Reviewable",
  "Approved",
  "Changes Requested",
] as const
export type ReviewStatus = (typeof reviewStatuses)[number]
export interface ReviewsSearch {
  tab?: "mine" | "all"
  repo?: string[]
  q?: string
  status?: ReviewStatus[]
  sort?: ReviewSort
  direction?: "asc" | "desc"
  page?: number
  // The open pull request, as `owner/repo#number` — the same shape as
  // `pullRequestKey`, so a row can put its own key straight into the URL.
  pr?: string
}

const prSelectionPattern = /^[\w.-]+\/[\w.-]+#\d+$/

export function parsePullRequestSelection(
  value: string | undefined
): { owner: string; repo: string; number: number } | null {
  if (!value || !prSelectionPattern.test(value)) return null
  const [fullName, rawNumber] = value.split("#")
  const [owner, repo] = fullName!.split("/")
  return { owner: owner!, repo: repo!, number: Number(rawNumber) }
}

const pullRequestUrlPattern =
  /github\.com\/([\w.-]+)\/([\w.-]+)\/pulls?\/(\d+)/i
const pullRequestRefPattern = /([\w.-]+)\/([\w.-]+)#(\d+)/

/** The pull request a pasted GitHub URL or `owner/repo#number` names, ignoring surrounding text. */
export function parsePullRequestReference(
  text: string
): { owner: string; repo: string; number: number } | null {
  const match =
    pullRequestUrlPattern.exec(text) ?? pullRequestRefPattern.exec(text)
  if (!match) return null
  const [, owner, repo, rawNumber] = match
  return { owner: owner!, repo: repo!, number: Number(rawNumber) }
}

export function validateReviewsSearch(
  search: Record<string, unknown>
): ReviewsSearch {
  const page = Number(search.page)
  const selectedStatuses = Array.isArray(search.status)
    ? search.status
    : [search.status]
  const status = reviewStatuses.filter((value) =>
    selectedStatuses.includes(value)
  )
  const selectedRepos = Array.isArray(search.repo) ? search.repo : [search.repo]
  const repo = [
    ...new Set(
      selectedRepos.filter(
        (value): value is string =>
          typeof value === "string" && value.length > 0 && value.length <= 140
      )
    ),
  ]
  return {
    tab: search.tab === "all" ? "all" : undefined,
    repo: repo.length ? repo : undefined,
    q: typeof search.q === "string" ? search.q.slice(0, 200) : undefined,
    status: status.length ? status : undefined,
    sort: (["updatedAt", "createdAt"] as const).find(
      (sort) => sort === search.sort
    ),
    direction:
      search.direction === "asc" || search.direction === "desc"
        ? search.direction
        : undefined,
    page: Number.isInteger(page) && page > 0 && page <= 50 ? page : undefined,
    pr:
      typeof search.pr === "string" && prSelectionPattern.test(search.pr)
        ? search.pr
        : undefined,
  }
}
