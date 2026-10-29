import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Input } from "@langchain/macaw-components/Input"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { useInfiniteQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useState } from "react"

import { useSession } from "@/lib/session"
import { OpenPullRequestInput } from "@/features/reviews/OpenPullRequestInput"
import { invalidationTopic } from "@/lib/invalidations/topics"
import {
  evidenceMediaUrl,
  frontendReviewApi,
  type FrontendReviewInboxItem,
  type FrontendReviewRun,
} from "./api"

type Scope = "mine" | "review-requested"
const PAGE_SIZE = 12

export function QAAgentPage() {
  const session = useSession()
  const [scope, setScope] = useState<Scope>("review-requested")

  return (
    <div className="min-h-0 min-w-0 flex-1 overflow-y-auto bg-surface-level-1">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-space-5 px-space-5 py-space-6 sm:px-space-6">
        <header className="grid items-start gap-space-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,24rem)]">
          <div className="max-w-xl">
            <h1 className="text-2xl font-semibold tracking-tight text-primary">
              Design QA
            </h1>
          </div>
          <div className="min-w-0">
            <OpenPullRequestInput frontend />
          </div>
        </header>

        <GroupedTabs<Scope>
          size="sm"
          className="w-fit"
          value={scope}
          onChange={setScope}
          options={[
            {
              value: "review-requested",
              display: "For you & your teams",
              "aria-label": `For you & your teams${scope === "review-requested" ? ", selected" : ""}`,
            },
            {
              value: "mine",
              display: "My PRs",
              "aria-label": `My PRs${scope === "mine" ? ", selected" : ""}`,
            },
          ]}
        />
        {session.data ? (
          <QAPullRequests
            key={scope}
            login={session.data.login}
            scope={scope}
          />
        ) : session.isError ? (
          <Card role="alert" className="p-space-5 text-sm text-error-secondary">
            Could not load your account. Refresh the page to try again.
          </Card>
        ) : (
          <Skeleton className="h-48 w-full" aria-label="Loading your account" />
        )}
      </div>
    </div>
  )
}

function QAPullRequests({ login, scope }: { login: string; scope: Scope }) {
  const query = useInfiniteQuery({
    queryKey: ["frontend-review-inbox", login, scope],
    queryFn: ({ pageParam }) => frontendReviewApi.inbox(scope, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last) => last.next_page ?? undefined,
    staleTime: 60_000,
    retry: false,
    meta: { invalidatedBy: [invalidationTopic("pull-requests")] },
  })
  const [limit, setLimit] = useState(PAGE_SIZE)
  const [search, setSearch] = useState("")
  const rows = query.data?.pages.flatMap((page) => page.items) ?? []
  const matching = rows.filter(({ pr }) =>
    `${pr.repo} #${pr.number} ${pr.title}`
      .toLowerCase()
      .includes(search.trim().toLowerCase())
  )
  const visible = matching.slice(0, limit)
  const more = limit < matching.length || query.hasNextPage
  const incomplete = query.data?.pages.some((page) => page.incomplete)

  const loadMore = async () => {
    if (limit >= matching.length && query.hasNextPage)
      await query.fetchNextPage()
    setLimit((previous) => previous + PAGE_SIZE)
  }

  return (
    <section
      aria-label={
        scope === "mine"
          ? "My PRs for Design QA"
          : "Requested PRs for Design QA"
      }
    >
      <p className="mb-space-3 text-xs text-secondary">
        {scope === "mine"
          ? "Your open PRs with saved Design QA runs."
          : "Open PRs with saved Design QA runs and a GitHub review request for you or a team you belong to. Comment mentions are not included."}
      </p>
      <div className="mb-space-4 flex flex-wrap items-center gap-space-3">
        <Input
          size="sm"
          className="min-w-40 flex-1 sm:max-w-sm"
          aria-label="Filter loaded pull requests"
          placeholder="Filter by title, repo, or number…"
          debounceMs={0}
          value={search}
          onChange={setSearch}
        />
        <span className="text-xs text-secondary" aria-live="polite">
          {query.isPending
            ? "Loading pull requests…"
            : `${visible.length} PR${visible.length === 1 ? "" : "s"} shown`}
        </span>
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          disabled={query.isFetching}
          onClick={() => {
            void query.refetch()
          }}
        >
          {query.isFetching && !query.isFetchingNextPage
            ? "Refreshing…"
            : "Refresh"}
        </Button>
      </div>

      {query.isError && (
        <Card
          role="alert"
          className="mb-space-4 p-space-4 text-sm text-error-secondary"
        >
          {rows.length > 0
            ? "Could not refresh every PR. Showing the loaded results. "
            : "Could not load pull requests. "}
          {query.error.message}
        </Card>
      )}
      {incomplete && (
        <p role="status" className="mb-space-4 text-sm text-warning-secondary">
          GitHub returned a partial list. Some pull requests may be missing;
          refresh to try again.
        </p>
      )}
      {query.isPending ? (
        <div
          aria-label="Loading pull requests"
          className="grid gap-space-4 sm:grid-cols-2 xl:grid-cols-3"
        >
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="h-64 w-full" />
          ))}
        </div>
      ) : visible.length === 0 && !query.isError ? (
        <Card className="flex flex-col items-center gap-space-2 p-space-8 text-center">
          <EyeIcon
            size={28}
            className="text-icon-secondary"
            aria-hidden="true"
          />
          <h2 className="text-sm font-medium text-primary">
            {search.trim()
              ? "No loaded PRs match your filter"
              : scope === "mine"
                ? "No saved Design QA reviews in your loaded PRs"
                : "No saved reviews in your loaded review requests"}
          </h2>
          <p className="max-w-sm text-sm text-secondary">
            {search.trim()
              ? "Try a different title, repository, or PR number."
              : more
                ? "Load more requested PRs below, or open any PR by number or link above."
                : "PRs appear here after Design QA runs and you or a team you belong to are requested as reviewers. Open any PR above to view its QA page."}
          </p>
        </Card>
      ) : (
        <div className="grid auto-rows-fr gap-space-4 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((item) => (
            <QAPullRequestCard
              key={`${item.pr.repo}#${item.pr.number}`}
              item={item}
              login={login}
            />
          ))}
        </div>
      )}
      {more && (
        <div className="mt-space-5 flex justify-center">
          <Button
            color="secondary"
            variant="outlined"
            disabled={query.isFetchingNextPage}
            onClick={() => void loadMore()}
          >
            {query.isFetchingNextPage ? "Loading more…" : "Load more PRs"}
          </Button>
        </div>
      )}
    </section>
  )
}

function reviewStatus(run: FrontendReviewRun | undefined, stale: boolean) {
  if (!run) return { label: "Not reviewed", color: "secondary" } as const
  if (run.status === "pending")
    return { label: "Queued", color: "secondary" } as const
  if (run.status === "running")
    return { label: "Capturing", color: "primary" } as const
  if (run.status === "blocked")
    return { label: "Capture blocked", color: "warning" } as const
  if (run.status === "failed")
    return { label: "Capture failed", color: "error" } as const
  if (stale) return { label: "Earlier commit", color: "warning" } as const
  if (run.report?.ai_review_status === "running")
    return { label: "AI reviewing", color: "primary" } as const
  if (run.report?.ai_review_status === "failed")
    return { label: "AI review failed", color: "warning" } as const
  if (run.report?.ai_review_status === "complete")
    return { label: "Review ready", color: "success" } as const
  return { label: "Screenshots ready", color: "secondary" } as const
}

function QAPullRequestCard({
  item,
  login,
}: {
  item: FrontendReviewInboxItem
  login: string
}) {
  const { pr, run } = item
  const [owner = "", repo = ""] = pr.repo.split("/")
  const stale = Boolean(pr.headSha && run.head_sha !== pr.headSha)
  const status = reviewStatus(run, stale)
  const scenarios = run.report?.scenarios ?? []
  const paired = scenarios.filter(
    (scenario) =>
      scenario.status === "complete" &&
      scenario.before_url &&
      scenario.after_url &&
      scenario.metrics
  )
  const maxRatio = paired.length
    ? Math.max(...paired.map((scenario) => scenario.metrics!.changed_ratio))
    : null
  const extent =
    maxRatio === null
      ? null
      : maxRatio === 0
        ? "No changes above threshold"
        : maxRatio < 0.02
          ? "Small visual change"
          : maxRatio < 0.15
            ? "Moderate visual change"
            : "Large visual change"
  const interactions = paired.filter((scenario) => scenario.interaction).length
  const requested = [
    ...(item.requested_reviewers.some(
      (reviewer) => reviewer.toLowerCase() === login.toLowerCase()
    )
      ? ["you"]
      : []),
    ...item.requested_teams.map((team) => `@${team}`),
  ]
  const scenario = paired.reduce<(typeof paired)[number] | undefined>(
    (largest, candidate) =>
      !largest ||
      candidate.metrics!.changed_ratio > largest.metrics!.changed_ratio
        ? candidate
        : largest,
    undefined
  )
  const thumbnail = scenario?.after_url
    ? evidenceMediaUrl(scenario.after_url)
    : null
  const summary =
    run?.error ||
    run?.report?.summary ||
    "Open this PR to inspect its capture details."

  return (
    <Link
      to="/agents/reviews/$owner/$repo/$number"
      params={{ owner, repo, number: String(pr.number) }}
      search={{ view: "frontend", run: run?.id }}
      className="block min-w-0 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-focus"
    >
      <Card className="flex h-full min-w-0 flex-col overflow-hidden p-0 transition-colors hover:border-strong">
        <div className="flex aspect-[16/6] items-center justify-center overflow-hidden border-b border-default bg-surface-level-3">
          {thumbnail ? (
            <img
              src={thumbnail}
              alt=""
              loading="lazy"
              className="h-full w-full object-cover object-top"
            />
          ) : (
            <div className="flex flex-col items-center gap-space-2 text-secondary">
              <EyeIcon size={28} aria-hidden="true" />
              <span className="text-xs">Capture details inside</span>
            </div>
          )}
        </div>
        <div className="flex flex-1 flex-col gap-space-2 p-space-4">
          <div className="flex flex-wrap items-center justify-between gap-space-2">
            <span
              className="min-w-0 truncate text-xs text-secondary"
              title={`${pr.repo} #${pr.number}`}
            >
              {pr.repo} #{pr.number}
            </span>
            {pr.draft && (
              <Badge color="secondary" size="xs">
                Draft
              </Badge>
            )}
          </div>
          <h2 className="line-clamp-2 text-sm font-semibold text-primary">
            {pr.title}
          </h2>
          <div>
            <Badge color={status.color} size="sm">
              {status.label}
            </Badge>
          </div>
          {requested.length > 0 && (
            <p
              className="truncate text-xs text-secondary"
              title={`Requested: ${requested.join(", ")}`}
            >
              Requested: {requested.join(", ")}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-space-2 text-xs text-secondary">
            {extent && (
              <Badge
                color="secondary"
                size="sm"
                title="Based on the largest changed-pixel percentage in a paired capture. Extent of change, not issue severity."
              >
                {extent}
              </Badge>
            )}
            {maxRatio !== null && (
              <span title="Largest changed-pixel percentage across paired states">
                {(maxRatio * 100).toFixed(2)}% pixels changed
              </span>
            )}
          </div>
          <p className="text-xs text-secondary">
            {scenarios.length
              ? `${paired.length}/${scenarios.length} states compared · ${interactions} interaction${interactions === 1 ? "" : "s"}`
              : "No states captured"}
          </p>
          <p className="line-clamp-3 text-xs leading-relaxed text-secondary">
            {summary}
          </p>
          {stale && (
            <p className="text-xs text-warning-secondary">
              Saved evidence is for an earlier commit.
            </p>
          )}
          <p className="text-xs text-tertiary">
            Run{" "}
            {new Date(run.created_at).toLocaleDateString(undefined, {
              month: "short",
              day: "numeric",
            })}
          </p>
          <span className="mt-auto flex items-center justify-between pt-space-2 text-xs font-medium text-primary">
            Open Design QA
            <ArrowUpRightIcon size={16} aria-hidden="true" />
          </span>
        </div>
      </Card>
    </Link>
  )
}
