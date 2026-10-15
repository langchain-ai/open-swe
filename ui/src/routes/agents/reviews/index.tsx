import { Button } from "@langchain/macaw-components/Button"
import { Text } from "@langchain/macaw-components/Text"
import { createFileRoute } from "@tanstack/react-router"
import { useEffect } from "react"

import { pageTitle } from "@/lib/pageTitle"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"
import { OpenPullRequests } from "@/features/reviews/OpenPullRequests"
import { ReviewedPullRequests } from "@/features/reviews/ReviewedPullRequests"
import { OpenPullRequestInput } from "@/features/reviews/OpenPullRequestInput"
import { ReviewBookmarklet } from "@/features/reviews/ReviewBookmarklet"
import {
  parsePullRequestSelection,
  validateReviewsSearch,
  type ReviewsSearch,
} from "@/features/reviews/search"

export const Route = createFileRoute("/agents/reviews/")({
  validateSearch: validateReviewsSearch,
  head: () => ({ meta: [{ title: pageTitle("Pull Requests") }] }),
  component: ReviewsPage,
})

const REPO_STORAGE_KEY = "open-swe.reviews.repo"

function readStoredRepos(): string[] | undefined {
  try {
    return validateReviewsSearch({
      repo: JSON.parse(window.localStorage.getItem(REPO_STORAGE_KEY) ?? "[]"),
    }).repo
  } catch (error) {
    console.warn("Discarding invalid stored repository filter", error)
    window.localStorage.removeItem(REPO_STORAGE_KEY)
    return undefined
  }
}

function storeRepos(repos: string[] | undefined) {
  if (repos?.length)
    window.localStorage.setItem(REPO_STORAGE_KEY, JSON.stringify(repos))
  else window.localStorage.removeItem(REPO_STORAGE_KEY)
}

const tabs = [
  ["mine", "Mine"],
  ["to-review", "To Review"],
  ["all", "All Reviews"],
] as const

function ReviewsPage() {
  const session = useSession()
  const filters = Route.useSearch()
  const navigate = Route.useNavigate()
  const tab = filters.tab ?? "mine"
  const changeFilters = (changes: Partial<ReviewsSearch>, replace = false) => {
    if ("repo" in changes) storeRepos(changes.repo)
    void navigate({
      search: (previous) => ({
        ...previous,
        ...changes,
        ...(Object.keys(changes).some((key) =>
          ["repo", "q", "status", "sort", "direction"].includes(key)
        )
          ? { page: undefined }
          : {}),
        ...("pr" in changes ? { files: undefined, at: undefined } : {}),
      }),
      replace,
    })
  }
  useEffect(() => {
    const stored = readStoredRepos()
    if (!filters.repo && stored) changeFilters({ repo: stored }, true)
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const selection = tab !== "all" ? parsePullRequestSelection(filters.pr) : null

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex min-h-0 w-full flex-1 flex-col px-space-5 py-space-5">
        <div
          className={cn(
            "mx-auto flex min-h-0 w-full flex-1 flex-col",
            !selection && "max-w-6xl"
          )}
        >
          <div className="flex items-center gap-space-3">
            <Text as="h1" variant="h3" weight="medium" color="primary">
              Pull Requests
            </Text>
            <div className="flex items-center gap-space-1">
              {tabs.map(([value, label]) => (
                <Button
                  key={value}
                  color="secondary"
                  variant={tab === value ? "normal" : "plain"}
                  aria-pressed={tab === value}
                  onClick={() => {
                    changeFilters({
                      tab: value === "mine" ? undefined : value,
                      page: undefined,
                      pr: undefined,
                    })
                  }}
                >
                  {label}
                </Button>
              ))}
            </div>
            <OpenPullRequestInput />
            <ReviewBookmarklet />
            <span className="hidden text-xs text-secondary lg:inline">
              Drag to your bookmarks bar
            </span>
          </div>

          {session.data &&
            (tab === "all" ? (
              <ReviewedPullRequests
                page={filters.page ?? 0}
                onPageChange={(page) =>
                  changeFilters({ page: page || undefined })
                }
              />
            ) : (
              <OpenPullRequests
                key={`${tab}-${Boolean(filters.github)}`}
                login={session.data.login}
                scope={
                  tab === "mine"
                    ? "mine"
                    : filters.github
                      ? "review-requested"
                      : "review-assigned"
                }
                filters={filters}
                onFiltersChange={changeFilters}
              />
            ))}
        </div>
      </div>
    </main>
  )
}
