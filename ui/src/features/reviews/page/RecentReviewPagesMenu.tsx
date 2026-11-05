import { Avatar } from "@langchain/macaw-components/Avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { CaretDownIcon } from "@langchain/macaw-components/icons"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useEffect, useState } from "react"

import { DropdownMenuItem } from "@/components/MenuItem"
import { api, type StoredPullRequest } from "@/lib/api"
import { useSession } from "@/lib/session"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import {
  readRecentReviewPages,
  recordRecentReviewPage,
  type RecentReviewPage,
} from "@/features/reviews/lib/recentReviewPages"
import { PullRequestStateBadge } from "./PullRequestStateBadge"
import { reviewQueries, type PullRequestRef } from "./queries"
import { RelativeTimestamp } from "./RelativeTimestamp"

const identity = (pr: { repo: string; number: number }) =>
  `${pr.repo}#${pr.number}`.toLowerCase()

/** Beside the PR's name in the header, opening the other review pages this person opened lately. */
export function RecentReviewPagesMenu({ pr }: { pr: PullRequestRef }) {
  const login = useSession().data?.login?.toLowerCase() ?? ""
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const detail = useQuery(reviewQueries.detail(pr)).data
  const recentKey = ["reviews", "recent", login]
  const recent = useQuery({
    queryKey: recentKey,
    queryFn: () => readRecentReviewPages(login),
    enabled: Boolean(login),
  })
  useEffect(() => {
    if (!login || !detail) return
    queryClient.setQueryData(
      ["reviews", "recent", login],
      recordRecentReviewPage(login, {
        ...pr,
        title: detail.pr.title,
        author: detail.pr.author?.login ?? detail.author,
        opened_at: new Date().toISOString(),
      })
    )
  }, [login, pr, detail, queryClient])
  const others = (recent.data ?? []).filter(
    (review) =>
      identity({
        repo: `${review.owner}/${review.repo}`,
        number: review.number,
      }) !== identity({ repo: `${pr.owner}/${pr.repo}`, number: pr.number })
  )
  const keys = others.map((review) => ({
    repo: `${review.owner}/${review.repo}`,
    number: review.number,
  }))
  const stored = useQuery({
    queryKey: ["reviews", "recent", "stored", keys.map(identity)],
    queryFn: () => api.lookupPullRequests(keys),
    enabled: open && keys.length > 0,
  })
  const storedByIdentity = new Map(
    (stored.data ?? []).map((row) => [identity(row), row])
  )
  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <IconButton
          icon={CaretDownIcon}
          iconWeight="bold"
          label="Recently opened reviews"
          size="sm"
          color="secondary"
          variant="outlined"
          className="shrink-0"
        />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="max-h-[min(32rem,70vh)] w-[min(36rem,calc(100vw-2rem))] overflow-y-auto"
      >
        <DropdownMenuLabel className="px-space-2 py-space-1 text-xxs text-tertiary">
          Recently opened
        </DropdownMenuLabel>
        {others.length === 0 ? (
          <p className="px-space-2 py-space-2 text-xs text-secondary">
            Other review pages you open show up here.
          </p>
        ) : (
          others.map((review) => (
            <RecentReviewRow
              key={`${review.owner}/${review.repo}#${review.number}`}
              review={review}
              stored={storedByIdentity.get(
                identity({
                  repo: `${review.owner}/${review.repo}`,
                  number: review.number,
                })
              )}
              login={login}
            />
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function RecentReviewRow({
  review,
  stored,
  login,
}: {
  review: RecentReviewPage
  stored: StoredPullRequest | undefined
  login: string
}) {
  const author = stored?.author || review.author
  const title = stored?.title || review.title
  return (
    <DropdownMenuItem asChild>
      <Link {...reviewPageRoute(review)} className="items-start">
        <Avatar
          size="sm"
          label={author}
          imageUrl={`https://github.com/${author}.png?size=40`}
        />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-space-1">
            <span className="truncate text-xs font-medium text-primary">
              {title || `${review.repo} #${review.number}`}
            </span>
            {stored && <PullRequestStateBadge state={stored.state} />}
          </span>
          <span className="block truncate text-xs text-secondary">
            {review.owner}/{review.repo}#{review.number} · by{" "}
            {author.toLowerCase() === login ? "you" : author}
            {stored?.additions != null && stored.deletions != null && (
              <>
                {" · "}
                <span className="font-mono tabular-nums">
                  <span className="text-success-secondary">
                    +{stored.additions}
                  </span>{" "}
                  <span className="text-error-secondary">
                    −{stored.deletions}
                  </span>
                </span>
              </>
            )}
            {stored?.last_reviewed_at && (
              <>
                {" · reviewed "}
                <RelativeTimestamp value={stored.last_reviewed_at} />
                {stored.last_review_findings != null &&
                  `, ${stored.last_review_findings} ${stored.last_review_findings === 1 ? "finding" : "findings"}`}
              </>
            )}
          </span>
        </span>
        <span className="shrink-0 text-xs text-tertiary">
          <RelativeTimestamp value={review.opened_at} />
        </span>
      </Link>
    </DropdownMenuItem>
  )
}
