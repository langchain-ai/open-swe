import { Avatar } from "@langchain/macaw-components/Avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { CaretDownIcon } from "@langchain/macaw-components/icons"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useEffect, type ReactNode } from "react"

import { DropdownMenuItem } from "@/components/MenuItem"
import { useSession } from "@/lib/session"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import {
  readRecentReviewPages,
  recordRecentReviewPage,
} from "@/features/reviews/lib/recentReviewPages"
import { reviewQueries, type PullRequestRef } from "./queries"
import { RelativeTimestamp } from "./RelativeTimestamp"

/** The PR's name in the header, opening the other review pages this person opened lately. */
export function RecentReviewPagesMenu({
  pr,
  children,
}: {
  pr: PullRequestRef
  children: ReactNode
}) {
  const login = useSession().data?.login?.toLowerCase() ?? ""
  const queryClient = useQueryClient()
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
      `${review.owner}/${review.repo}#${review.number}`.toLowerCase() !==
      `${pr.owner}/${pr.repo}#${pr.number}`.toLowerCase()
  )
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="group flex min-w-0 items-center gap-space-1 rounded-sm text-left hover:underline"
        >
          <span className="min-w-0 truncate">{children}</span>
          <CaretDownIcon
            aria-label="Recently opened reviews"
            size={14}
            className="shrink-0 text-icon-secondary group-hover:text-icon-primary"
          />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="max-h-[min(28rem,70vh)] w-[min(32rem,calc(100vw-2rem))] overflow-y-auto"
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
            <DropdownMenuItem
              key={`${review.owner}/${review.repo}#${review.number}`}
              asChild
            >
              <Link {...reviewPageRoute(review)} className="items-start">
                <Avatar
                  size="sm"
                  label={review.author}
                  imageUrl={`https://github.com/${review.author}.png?size=40`}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium text-primary">
                    {review.title || `${review.repo} #${review.number}`}
                  </span>
                  <span className="block truncate text-xs text-secondary">
                    {review.owner}/{review.repo}#{review.number} · by{" "}
                    {review.author.toLowerCase() === login
                      ? "you"
                      : review.author}
                  </span>
                </span>
                <span className="shrink-0 text-xs text-tertiary">
                  <RelativeTimestamp value={review.opened_at} />
                </span>
              </Link>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
