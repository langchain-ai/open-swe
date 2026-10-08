import { Button } from "@langchain/macaw-components/Button"
import { createFileRoute } from "@tanstack/react-router"

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
  const selection = tab !== "all" ? parsePullRequestSelection(filters.pr) : null

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex min-h-0 w-full flex-1 flex-col px-6 py-6">
        <div
          className={cn(
            "mx-auto flex min-h-0 w-full flex-1 flex-col",
            !selection && "max-w-6xl"
          )}
        >
          <div className="flex items-center gap-3">
            <h1 className="font-heading text-base font-medium text-primary">
              Pull Requests
            </h1>
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
