import { queryOptions } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"

/** A pull request's topic; GitHub names are case-insensitive. */
function pullRequestTopic(owner: string, repo: string, number: number) {
  return invalidationTopic(
    "pull-request",
    `${owner.toLowerCase()}/${repo.toLowerCase()}/${number}`
  )
}

/** The review page payload; its pull request's topic refetches it when the mirror changes. */
export function reviewDetailQuery(owner: string, repo: string, number: number) {
  return queryOptions({
    queryKey: ["review", owner, repo, number],
    queryFn: () => api.getReview(owner, repo, number),
    meta: { invalidatedBy: [pullRequestTopic(owner, repo, number)] },
  })
}

/** The PR's listed files; its topic refetches them once a new head is listed. */
export function reviewDiffQuery(owner: string, repo: string, number: number) {
  return queryOptions({
    queryKey: ["reviewDiff", owner, repo, number],
    queryFn: () => api.getReviewDiff(owner, repo, number),
    meta: { invalidatedBy: [pullRequestTopic(owner, repo, number)] },
  })
}
