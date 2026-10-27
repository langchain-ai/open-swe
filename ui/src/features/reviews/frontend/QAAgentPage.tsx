import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Input } from "@langchain/macaw-components/Input"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useState } from "react"

import type { OpenPullRequest } from "@/lib/api"
import { useSession } from "@/lib/session"
import { OpenPullRequestInput } from "@/features/reviews/OpenPullRequestInput"
import { sharedReviewOptions } from "@/features/reviews/lib/reviewKeys"
import { useOpenPullRequests } from "@/features/reviews/lib/useOpenPullRequests"
import {
  evidenceMediaUrl,
  frontendReviewApi,
  frontendReviewKey,
  type FrontendReviewRun,
} from "./api"

type Scope = "mine" | "review-assigned"
const PAGE_SIZE = 12

export function QAAgentPage() {
  const session = useSession()
  const [scope, setScope] = useState<Scope>("mine")

  return (
    <div className="min-h-0 min-w-0 flex-1 overflow-y-auto bg-surface-level-1">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-space-6 px-space-5 py-space-6 sm:px-space-6">
        <header className="flex flex-wrap items-start justify-between gap-space-4">
          <div className="max-w-xl">
            <div className="mb-space-2 flex items-center gap-space-2 text-secondary">
              <EyeIcon size={20} aria-hidden="true" />
              <span className="text-xs font-medium">Frontend review</span>
            </div>
            <h1 className="text-2xl font-semibold tracking-tight text-primary">
              QA agent
            </h1>
            <p className="mt-space-2 text-sm text-secondary">
              Review how your pull requests look. Open a PR to compare
              screenshots, inspect pixel changes, and explore the agent’s visual
              findings.
            </p>
          </div>
          <div className="w-full pt-space-1 sm:w-auto">
            <OpenPullRequestInput frontend />
          </div>
        </header>

        <Card className="p-space-4 text-sm text-secondary">
          Saved evidence is available. Live captures are paused until the
          browser’s network isolation is configured.
        </Card>

        <GroupedTabs<Scope>
          size="sm"
          className="w-fit"
          value={scope}
          onChange={setScope}
          options={[
            { value: "mine", display: "My PRs" },
            { value: "review-assigned", display: "To review" },
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
  const client = useQueryClient()
  const query = useOpenPullRequests(login, [], "updatedAt", "desc", scope)
  const [limit, setLimit] = useState(PAGE_SIZE)
  const [search, setSearch] = useState("")
  const rows = query.data?.pages.flatMap((page) => page.pullRequests) ?? []
  const matching = rows.filter((pr) =>
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
          ? "My PRs for frontend QA"
          : "Assigned PRs for frontend QA"
      }
    >
      <div className="mb-space-4 flex flex-wrap items-center gap-space-3">
        <Input
          size="sm"
          className="min-w-40 flex-1 sm:max-w-sm"
          aria-label="Filter loaded pull requests"
          placeholder="Filter loaded PRs by title, repo, or number…"
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
            void client.invalidateQueries({ queryKey: ["frontend-review"] })
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
                ? "No open pull requests yet"
                : "No Open SWE review assignments"}
          </h2>
          <p className="max-w-sm text-sm text-secondary">
            {search.trim()
              ? "Try a different title, repository, or PR number. You can also load more PRs below."
              : "Paste a GitHub PR link above to open its frontend QA page."}
          </p>
        </Card>
      ) : (
        <div className="grid auto-rows-fr gap-space-4 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((pr) => (
            <QAPullRequestCard key={`${pr.repo}#${pr.number}`} pr={pr} />
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

function QAPullRequestCard({ pr }: { pr: OpenPullRequest }) {
  const [owner = "", repo = ""] = pr.repo.split("/")
  const ref = { owner, repo, number: pr.number }
  const evidence = useQuery({
    queryKey: frontendReviewKey(ref),
    queryFn: () => frontendReviewApi.list(ref),
    ...sharedReviewOptions(ref),
  })
  const run = evidence.data?.runs[0]
  const stale = Boolean(run && pr.headSha && run.head_sha !== pr.headSha)
  const status = evidence.isError
    ? { label: "Evidence unavailable", color: "warning" as const }
    : reviewStatus(run, stale)
  const scenario = run?.report?.scenarios.find((page) => page.after_url)
  const thumbnail = scenario?.after_url
    ? evidenceMediaUrl(scenario.after_url)
    : null
  const summary =
    run?.error ||
    run?.report?.summary ||
    (evidence.isError
      ? "Open this PR to retry loading its saved evidence."
      : "Start a visual review to capture before and after screenshots for this PR.")

  return (
    <Link
      to="/agents/reviews/$owner/$repo/$number"
      params={{ owner, repo, number: String(pr.number) }}
      search={{ view: "frontend", run: run?.id }}
      className="block min-w-0 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-focus"
    >
      <Card className="flex h-full min-w-0 flex-col overflow-hidden p-0 transition-colors hover:border-strong">
        <div className="flex aspect-[16/7] items-center justify-center overflow-hidden border-b border-default bg-surface-level-2">
          {evidence.isPending ? (
            <Skeleton className="h-full w-full" />
          ) : thumbnail ? (
            <img
              src={thumbnail}
              alt=""
              loading="lazy"
              className="h-full w-full object-cover object-top"
            />
          ) : (
            <div className="flex flex-col items-center gap-space-2 text-secondary">
              <EyeIcon size={28} aria-hidden="true" />
              <span className="text-xs">
                {run ? "Capture details inside" : "Your next visual review"}
              </span>
            </div>
          )}
        </div>
        <div className="flex flex-1 flex-col gap-space-3 p-space-4">
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
              {evidence.isPending ? "Loading evidence…" : status.label}
            </Badge>
          </div>
          <p className="line-clamp-3 text-xs leading-relaxed text-secondary">
            {summary}
          </p>
          {stale && (
            <p className="text-xs text-warning-secondary">
              Saved evidence is for an earlier commit.
            </p>
          )}
          <span className="mt-auto flex items-center justify-between pt-space-2 text-xs font-medium text-primary">
            {run ? "Open frontend QA" : "Start frontend QA"}
            <ArrowUpRightIcon size={16} aria-hidden="true" />
          </span>
        </div>
      </Card>
    </Link>
  )
}
