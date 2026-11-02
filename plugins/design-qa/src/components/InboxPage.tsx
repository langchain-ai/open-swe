import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Input } from "@langchain/macaw-components/Input"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import type {
  FrontendReviewInboxItem,
  FrontendReviewRun,
} from "@openswe/plugin-sdk"
import { useHostContext } from "@openswe/plugin-sdk/react"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { useState } from "react"

import { EXTENT_LABELS, runOverview } from "../lib/coverage"
import { useImage, useNearViewport } from "../lib/media"
import { splitRepository } from "../lib/pullRequest"
import {
  errorMessage,
  isForbidden,
  useInbox,
  useMedia,
  useRepositories,
  useShareRepositories,
  useTitle,
  type InboxScope,
  type RunMedia,
} from "../lib/queries"
import { RouteLink } from "../lib/router"
import { OpenPullRequestInput } from "./OpenPullRequestInput"
import { ShareRepositories } from "./ShareRepositories"

/** Open pull requests with saved Design QA runs, as cards. */
export function InboxPage() {
  useTitle(null)
  const repositories = useRepositories()
  const share = useShareRepositories()
  const [scope, setScope] = useState<InboxScope>("review-requested")
  const shared = repositories.data?.map((one) => one.fullName) ?? []

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-space-5 px-space-5 py-space-6 sm:px-space-6">
      <header className="grid items-start gap-space-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,24rem)]">
        <div className="max-w-xl">
          <h1 className="text-2xl font-semibold tracking-tight text-primary">
            Design QA
          </h1>
        </div>
        <div className="min-w-0">
          <OpenPullRequestInput shared={shared} />
        </div>
      </header>

      {repositories.isPending ? (
        <Skeleton
          className="h-48 w-full"
          aria-label="Loading your repositories"
        />
      ) : repositories.isError && !isForbidden(repositories.error) ? (
        <Card role="alert" className="p-space-5 text-sm text-error-secondary">
          Could not load your repositories: {errorMessage(repositories.error)}
        </Card>
      ) : shared.length === 0 ? (
        <ShareRepositories unshared={repositories.isError} />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-space-3">
            <GroupedTabs<InboxScope>
              size="sm"
              className="w-fit"
              value={scope}
              onChange={setScope}
              options={[
                {
                  value: "review-requested",
                  display: "Requested from you",
                  "aria-label": `Requested from you${scope === "review-requested" ? ", selected" : ""}`,
                },
                {
                  value: "mine",
                  display: "My PRs",
                  "aria-label": `My PRs${scope === "mine" ? ", selected" : ""}`,
                },
              ]}
            />
            <Button
              size="sm"
              color="secondary"
              variant="plain"
              loading={share.isPending}
              onClick={() => share.mutate(undefined)}
            >
              Share repositories
            </Button>
          </div>
          <PullRequests key={scope} scope={scope} />
        </>
      )}
    </main>
  )
}

function PullRequests({ scope }: { scope: InboxScope }) {
  const { user } = useHostContext()
  const inbox = useInbox(scope)
  const [search, setSearch] = useState("")
  if (isForbidden(inbox.error)) return <ShareRepositories unshared />

  const rows = inbox.data?.items ?? []
  const filter = search.trim().toLowerCase()
  const visible = rows.filter(({ pr }) =>
    `${pr.repo} #${pr.number} ${pr.title}`.toLowerCase().includes(filter)
  )

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
          ? "Your open PRs with saved Design QA runs, in the repositories you shared."
          : "Open PRs with saved Design QA runs and a GitHub review request for you or a team you belong to, in the repositories you shared. Comment mentions are not included."}
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
          {inbox.isPending
            ? "Loading pull requests…"
            : `${visible.length} PR${visible.length === 1 ? "" : "s"} shown`}
        </span>
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          disabled={inbox.isFetching}
          onClick={() => {
            void inbox.refetch()
          }}
        >
          {inbox.isFetching ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {inbox.isError && (
        <Card
          role="alert"
          className="mb-space-4 p-space-4 text-sm text-error-secondary"
        >
          {rows.length > 0
            ? "Could not refresh every PR. Showing the loaded results. "
            : "Could not load pull requests. "}
          {errorMessage(inbox.error)}
        </Card>
      )}
      {inbox.data?.incomplete && (
        <p role="status" className="mb-space-4 text-sm text-warning-secondary">
          GitHub returned a partial list. Some pull requests may be missing;
          refresh to try again.
        </p>
      )}
      {inbox.isPending ? (
        <div
          aria-label="Loading pull requests"
          className="grid gap-space-4 sm:grid-cols-2 xl:grid-cols-3"
        >
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="h-64 w-full" />
          ))}
        </div>
      ) : visible.length === 0 && !inbox.isError ? (
        <Card className="flex flex-col items-center gap-space-2 p-space-8 text-center">
          <EyeIcon
            size={28}
            className="text-icon-secondary"
            aria-hidden="true"
          />
          <h2 className="text-sm font-medium text-primary">
            {filter
              ? "No loaded PRs match your filter"
              : scope === "mine"
                ? "No saved Design QA reviews in your PRs"
                : "No saved reviews in your review requests"}
          </h2>
          <p className="max-w-sm text-sm text-secondary">
            {filter
              ? "Try a different title, repository, or PR number."
              : scope === "mine"
                ? "Your open PRs appear here after Design QA runs on them. Open any PR above to view its QA page."
                : "PRs appear here after Design QA runs and you or a team you belong to are requested as reviewers. Open any PR above to view its QA page."}
          </p>
        </Card>
      ) : (
        <div className="grid auto-rows-fr gap-space-4 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((item) => (
            <PullRequestCard
              key={`${item.pr.repo}#${item.pr.number}`}
              item={item}
              login={user.login}
            />
          ))}
        </div>
      )}
    </section>
  )
}

function reviewStatus(run: FrontendReviewRun, stale: boolean) {
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

function PullRequestCard({
  item,
  login,
}: {
  item: FrontendReviewInboxItem
  login: string
}) {
  const { pr, run } = item
  const repository = splitRepository(pr.repo)
  const stale = Boolean(pr.headSha && run.head_sha !== pr.headSha)
  const status = reviewStatus(run, stale)
  const overview = runOverview(run)
  const maxRatio = overview.largest?.metrics.changed_ratio
  const requested = [
    ...(item.requested_reviewers.some(
      (reviewer) => reviewer.toLowerCase() === login.toLowerCase()
    )
      ? ["you"]
      : []),
    ...item.requested_teams.map((team) => `@${team}`),
  ]
  const summary =
    run.error ||
    run.report?.summary ||
    "Open this PR to inspect its capture details."
  if (!repository) return null

  return (
    <RouteLink
      to={{
        page: "pull",
        pr: { ...repository, number: pr.number },
        run: run.id,
      }}
      className="block min-w-0 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-focus"
    >
      <Card className="flex h-full min-w-0 flex-col overflow-hidden p-0 transition-colors hover:border-strong">
        <div className="flex aspect-[16/6] items-center justify-center overflow-hidden border-b border-default bg-surface-level-3">
          <Thumbnail
            media={{ repository: pr.repo, pull: pr.number, run: run.id }}
            name={overview.largest?.after_url ?? null}
          />
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
            {overview.extent && (
              <Badge
                color="secondary"
                size="sm"
                title="Based on the largest changed-pixel percentage in a paired capture. Extent of change, not issue severity."
              >
                {EXTENT_LABELS[overview.extent]}
              </Badge>
            )}
            {maxRatio !== undefined && (
              <span title="Largest changed-pixel percentage across paired states">
                {(maxRatio * 100).toFixed(2)}% pixels changed
              </span>
            )}
          </div>
          <p className="text-xs text-secondary">
            {overview.total
              ? `${overview.paired}/${overview.total} states compared · ${overview.interactions} interaction${overview.interactions === 1 ? "" : "s"}`
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
    </RouteLink>
  )
}

/** The PR screenshot of the state that changed most, read once the card nears the screen. */
function Thumbnail({ media, name }: { media: RunMedia; name: string | null }) {
  const [container, near] = useNearViewport<HTMLDivElement>()
  const file = useMedia(media, name, near)
  const { image } = useImage(file.data)
  return (
    <div
      ref={container}
      className="flex h-full w-full items-center justify-center"
    >
      {image ? (
        <img
          src={image.url}
          alt=""
          className="h-full w-full object-cover object-top"
        />
      ) : (
        <div className="flex flex-col items-center gap-space-2 text-secondary">
          <EyeIcon size={28} aria-hidden="true" />
          <span className="text-xs">Capture details inside</span>
        </div>
      )}
    </div>
  )
}
