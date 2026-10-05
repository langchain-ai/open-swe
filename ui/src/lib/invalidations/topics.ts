/**
 * Topics the backend invalidates when data changes
 * (`agent/ui_invalidations/topics.py`). A query lists the ones it reads in
 * `meta.invalidatedBy`; an invalidation of any of them refetches it, so it
 * never needs a `refetchInterval`.
 */
export const INVALIDATION_TOPICS = {
  workspaces: "workspaces",
} as const

export type InvalidationTopic = string

/** One pull request's mirrored details, files and check runs. */
export function pullRequestTopic(
  owner: string,
  repo: string,
  number: number
): InvalidationTopic {
  return `pull-request/${owner.toLowerCase()}/${repo.toLowerCase()}/${number}`
}
