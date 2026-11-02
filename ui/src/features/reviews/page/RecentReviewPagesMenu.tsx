import { Avatar } from "@langchain/macaw-components/Avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { Button } from "@langchain/macaw-components/Button"
import {
  CaretDownIcon,
  ClockCounterClockwiseRegularIcon,
} from "@langchain/macaw-components/icons"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useRouterState } from "@tanstack/react-router"
import { useEffect, type ReactNode } from "react"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"

import { cn } from "@/lib/utils"

import { DropdownMenuItem } from "@/components/MenuItem"
import { useSession } from "@/lib/session"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import {
  readRecentReviewPages,
  recordRecentReviewPage,
  type RecentReviewPage,
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
  const recent = useRecentReviewPages(login)
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
        <a
          href={githubUrls.pullRequest(pr)}
          target="_blank"
          rel="noreferrer"
          onPointerDown={(event) => {
            if (event.metaKey || event.ctrlKey) event.preventDefault()
          }}
          onClick={(event) => {
            if (!event.metaKey && !event.ctrlKey) event.preventDefault()
          }}
          className="group flex min-w-0 items-center gap-space-1 rounded-sm text-left hover:underline"
        >
          <span className="min-w-0 truncate">{children}</span>
          <CaretDownIcon
            aria-label="Recently opened reviews"
            size={14}
            className="shrink-0 text-icon-secondary group-hover:text-icon-primary"
          />
        </a>
      </DropdownMenuTrigger>
      <RecentReviewPagesContent login={login} reviews={others} />
    </DropdownMenu>
  )
}

export function useRecentReviewPages(login: string) {
  return useQuery({
    queryKey: ["reviews", "recent", login],
    queryFn: () => readRecentReviewPages(login),
    enabled: Boolean(login),
  })
}

/** Reopens a review page this person opened lately, from the pull request list. */
export function RecentReviewPagesButton() {
  const login = useSession().data?.login?.toLowerCase() ?? ""
  const recent = useRecentReviewPages(login).data ?? []
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          leftDecorator={ClockCounterClockwiseRegularIcon}
          rightDecorator={CaretDownIcon}
        >
          Recent
        </Button>
      </DropdownMenuTrigger>
      <RecentReviewPagesContent login={login} reviews={recent} />
    </DropdownMenu>
  )
}

function RecentReviewPagesContent({
  login,
  reviews,
}: {
  login: string
  reviews: Array<RecentReviewPage>
}) {
  return (
    <DropdownMenuContent
      align="start"
      className="max-h-[min(28rem,70vh)] w-[min(32rem,calc(100vw-2rem))] overflow-y-auto"
    >
      <DropdownMenuLabel className="px-space-2 py-space-1 text-xxs text-tertiary">
        Recently opened
      </DropdownMenuLabel>
      {reviews.length === 0 ? (
        <p className="px-space-2 py-space-2 text-xs text-secondary">
          Review pages you open show up here.
        </p>
      ) : (
        reviews.map((review) => (
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
  )
}

function reviewName(review: RecentReviewPage) {
  return review.title || `${review.repo} #${review.number}`
}

/** The review pages this person opened lately, kept in the sidebar on every page. */
export function RecentReviewsSidebarSection({
  onNavigate,
}: {
  onNavigate?: () => void
}) {
  const login = useSession().data?.login?.toLowerCase() ?? ""
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const recent = (useRecentReviewPages(login).data ?? []).slice(0, 5)
  if (!recent.length) return null
  return (
    <div className="mb-space-4 shrink-0">
      <p className="mb-space-2 px-space-2 py-space-1 text-xs font-medium text-tertiary">
        Reviewing
      </p>
      {recent.map((review) => {
        const route = reviewPageRoute(review)
        const active =
          pathname.toLowerCase() ===
          `/agents/reviews/${review.owner}/${review.repo}/${review.number}`.toLowerCase()
        return (
          <Link
            key={`${review.owner}/${review.repo}#${review.number}`}
            {...route}
            onClick={onNavigate}
            title={`${reviewName(review)} · ${review.owner}/${review.repo}#${review.number}`}
            className={cn(
              "flex h-8 items-center gap-space-2 rounded-md px-space-2 text-sm text-secondary transition-colors hover:bg-surface-level-3 hover:text-primary",
              active && "bg-selected text-primary hover:bg-selected-hover"
            )}
          >
            <GitPullRequestIcon
              size={16}
              className="shrink-0 text-icon-secondary"
            />
            <span className="min-w-0 flex-1 truncate">
              {reviewName(review)}
            </span>
            <span className="shrink-0 text-xs text-tertiary">
              #{review.number}
            </span>
          </Link>
        )
      })}
    </div>
  )
}

/** Cards for the reviews this person was in the middle of, atop the pull request list. */
export function ContinueReviewingStrip() {
  const login = useSession().data?.login?.toLowerCase() ?? ""
  const recent = (useRecentReviewPages(login).data ?? []).slice(0, 3)
  if (!recent.length) return null
  return (
    <section className="mt-space-4 flex flex-col gap-space-2">
      <p className="text-xs font-medium text-tertiary">Continue reviewing</p>
      <div className="grid grid-cols-1 gap-space-2 md:grid-cols-3">
        {recent.map((review) => (
          <Link
            key={`${review.owner}/${review.repo}#${review.number}`}
            {...reviewPageRoute(review)}
            className="flex min-w-0 items-start gap-space-3 rounded-lg border border-default bg-surface-level-2 p-space-3 transition-colors hover:bg-surface-level-2-hover"
          >
            <Avatar
              size="sm"
              label={review.author}
              imageUrl={`https://github.com/${review.author}.png?size=40`}
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-primary">
                {reviewName(review)}
              </span>
              <span className="block truncate text-xs text-secondary">
                {review.owner}/{review.repo}#{review.number} · by{" "}
                {review.author} · <RelativeTimestamp value={review.opened_at} />
              </span>
            </span>
          </Link>
        ))}
      </div>
    </section>
  )
}
