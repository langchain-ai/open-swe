import { Button } from "@langchain/macaw-components/Button"
import { ArrowLeftIcon } from "@langchain/macaw-components/icons"
import { Text } from "@langchain/macaw-components/Text"
import { createFileRoute, Link, redirect } from "@tanstack/react-router"
import { useEffect } from "react"

import { pageTitle } from "@/lib/pageTitle"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"
import { OpenPullRequests } from "@/features/reviews/OpenPullRequests"
import { ReviewedPullRequests } from "@/features/reviews/ReviewedPullRequests"
import { OpenPullRequestInput } from "@/features/reviews/OpenPullRequestInput"
import { PullRequestWorkspace } from "@/features/reviews/PullRequestWorkspace"
import { ReviewBookmarklet } from "@/features/reviews/ReviewBookmarklet"
import {
  parsePullRequestSelection,
  validateReviewsSearch,
  type ReviewsSearch,
} from "@/features/reviews/search"

export const Route = createFileRoute("/agents/reviews/")({
  validateSearch: validateReviewsSearch,
  ssr: false,
  beforeLoad: ({ search, location }) => {
    const stored = readStoredRepos()
    const storedTab = validateReviewsSearch({
      tab: window.localStorage.getItem(TAB_STORAGE_KEY),
    }).tab
    const changes: Partial<ReviewsSearch> = {
      ...(!search.repo && stored ? { repo: stored } : {}),
      ...(!search.tab ? { tab: storedTab ?? "mine" } : {}),
    }
    if (Object.keys(changes).length)
      throw redirect({
        to: "/agents/reviews",
        search: { ...search, ...changes },
        hash: location.hash,
        replace: true,
      })
  },
  head: () => ({ meta: [{ title: pageTitle("Pull Requests") }] }),
  component: ReviewsPage,
})

const REPO_STORAGE_KEY = "open-swe.reviews.repo"
const TAB_STORAGE_KEY = "open-swe.reviews.tab"

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
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const changeFilters = (changes: Partial<ReviewsSearch>, replace = false) => {
    if ("repo" in changes) storeRepos(changes.repo)
    void navigate({
      replace,
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
    })
  }
  useEffect(() => {
    if (filters.tab) window.localStorage.setItem(TAB_STORAGE_KEY, filters.tab)
  }, [filters.tab])
  const selection = parsePullRequestSelection(filters.pr)

  return (
    <main
      className={cn(
        "flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden",
        isDesktop && "pt-11"
      )}
    >
      <div
        className={cn(
          "flex min-h-0 w-full flex-1 flex-col",
          !selection && "px-space-5 py-space-5"
        )}
      >
        <div
          className={cn(
            "mx-auto flex min-h-0 w-full flex-1 flex-col",
            !selection && "max-w-6xl"
          )}
        >
          <div
            className={cn(
              "flex flex-wrap items-center gap-space-3",
              selection && "border-b border-muted px-space-3 py-space-2"
            )}
          >
            <Button
              as={<Link to="/agents" />}
              size="sm"
              color="secondary"
              variant="plain"
              leftDecorator={ArrowLeftIcon}
              className="border-0"
            >
              Back to threads
            </Button>
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
                      tab: value,
                      page: undefined,
                      pr: undefined,
                      queue: undefined,
                    })
                  }}
                >
                  {label}
                </Button>
              ))}
            </div>
            <OpenPullRequestInput />
            {!selection && (
              <>
                <ReviewBookmarklet />
                <span className="hidden text-xs text-secondary lg:inline">
                  Drag to your bookmarks bar
                </span>
              </>
            )}
          </div>

          {session.data &&
            (tab === "all" ? (
              <PullRequestWorkspace
                pr={selection}
                onClose={() =>
                  changeFilters({ pr: undefined, queue: undefined })
                }
              >
                {(onSelect) => (
                  <ReviewedPullRequests
                    page={filters.page ?? 0}
                    selected={filters.pr}
                    onSelect={(pr) => {
                      changeFilters({ pr, queue: undefined })
                      onSelect()
                    }}
                    onPageChange={(page) =>
                      changeFilters({ page: page || undefined })
                    }
                  />
                )}
              </PullRequestWorkspace>
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
