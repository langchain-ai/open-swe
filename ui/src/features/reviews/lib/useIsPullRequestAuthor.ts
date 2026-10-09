import { useQuery } from "@tanstack/react-query"

import { useSession } from "@/lib/session"
import { sameLogin } from "@/features/reviews/lib/logins"
import { reviewQueries } from "@/features/reviews/page/queries"

/** Whether the signed-in person opened this pull request; GitHub won't let them approve it. */
export function useIsPullRequestAuthor(
  owner: string,
  repo: string,
  number: number
): boolean {
  const viewer = useSession().data?.login
  const author = useQuery(reviewQueries.detail({ owner, repo, number })).data
    ?.pr.author?.login
  return sameLogin(author, viewer)
}
