import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Input } from "@langchain/macaw-components/Input"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useHostContext } from "@openswe/plugin-sdk/react"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { useState } from "react"

import type { FrontendReviewRun } from "../lib/capture"
import { runOverview } from "../lib/coverage"
import { useImage, useNearViewport } from "../lib/media"
import { splitRepository } from "../lib/pullRequest"
import {
  errorMessage,
  isForbidden,
  useInbox,
  useInboxChanges,
  useMedia,
  useRepositories,
  useShareRepositories,
  useTitle,
  type InboxItem,
  type InboxScope,
  type RunMedia,
} from "../lib/queries"
import { RouteLink } from "../lib/router"
import { OpenPullRequestInput } from "./OpenPullRequestInput"
import { ShareRepositories } from "./ShareRepositories"
import { CaptureAction } from "./CaptureAction"

/** Open pull requests and their capture status, as cards. */
export function InboxPage() {
  useTitle(null)
  const repositories = useRepositories()
  const share = useShareRepositories()
  const [scope, setScope] = useState<InboxScope>("review-requested")
  const shared = repositories.data?.map((one) => one.fullName) ?? []
  useInboxChanges(shared)

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
  const visible = rows.filter(({ repository, pull }) =>
    `${repository} #${pull.number} ${pull.title}`.toLowerCase().includes(filter)
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
          ? "Your open PRs and their capture status, in the repositories you shared."
          : "Open PRs with a GitHub review request for you or a team you belong to, in the repositories you shared. Comment mentions are not included."}
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
                ? "No open PRs in your shared repositories"
                : "No open PRs requesting your review"}
          </h2>
          <p className="max-w-sm text-sm text-secondary">
            {filter
              ? "Try a different title, repository, or PR number."
              : scope === "mine"
                ? "Your open PRs appear here, including those without captures. Open any PR above to view its QA page."
                : "PRs appear here when you or a team you belong to are requested as reviewers. Open any PR above to view its QA page."}
          </p>
        </Card>
      ) : (
        <div className="grid auto-rows-fr gap-space-4 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((item) => (
            <PullRequestCard
              key={`${item.repository}#${item.pull.number}`}
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

function PullRequestCard({ item, login }: { item: InboxItem; login: string }) {
  const { pull: pr, run } = item
  const repository = splitRepository(item.repository)
  const stale = Boolean(
    run && "headSha" in pr && pr.headSha && run.head_sha !== pr.headSha
  )
  const status = run
    ? reviewStatus(run, stale)
    : { label: "Not captured", color: "secondary" as const }
  const overview = run ? runOverview(run) : null
  const requested = [
    ...("requestedReviewers" in pr &&
    pr.requestedReviewers.some(
      (reviewer) => reviewer.toLowerCase() === login.toLowerCase()
    )
      ? ["you"]
      : []),
    ...("requestedTeams" in pr
      ? pr.requestedTeams.map((team) => `@${team}`)
      : []),
  ]
  const summary =
    item.outcome?.reason ||
    run?.error ||
    run?.report?.summary ||
    "Open this PR to inspect its capture details."
  if (!repository) return null

  return (
    <Card className="flex h-full min-w-0 flex-col overflow-hidden p-0">
      <RouteLink
        to={{
          page: "pull",
          pr: { ...repository, number: pr.number },
          run: run?.id ?? null,
        }}
        className="flex h-full min-w-0 flex-col rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-focus"
      >
        <div className="flex aspect-[16/6] items-center justify-center overflow-hidden border-b border-default bg-surface-level-3">
          <Thumbnail
            media={{
              repository: item.repository,
              pull: pr.number,
              run: run?.id ?? "",
            }}
            name={overview?.cover?.after_url ?? null}
          />
        </div>
        <div className="flex flex-1 flex-col gap-space-2 p-space-4">
          <div className="flex flex-wrap items-center justify-between gap-space-2">
            <span
              className="min-w-0 truncate text-xs text-secondary"
              title={`${item.repository} #${pr.number}`}
            >
              {item.repository} #{pr.number}
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
          <p className="text-xs text-secondary">
            {overview?.total
              ? `${overview.paired}/${overview?.total} states captured on both sides · ${overview.interactions} interaction${overview.interactions === 1 ? "" : "s"}`
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
            {run
              ? `Run ${new Date(run.created_at).toLocaleDateString()}`
              : "No saved screenshots yet"}
          </p>
          <span className="mt-auto flex items-center justify-between pt-space-2 text-xs font-medium text-primary">
            Open Design QA
            <ArrowUpRightIcon size={16} aria-hidden="true" />
          </span>
        </div>
      </RouteLink>
      <div className="mt-auto p-space-4 pt-0">
        <CaptureAction
          pr={{ ...repository, number: pr.number }}
          outcome={item.outcome}
          currentHeadSha={"headSha" in pr ? pr.headSha : undefined}
          hasCapture={Boolean(run)}
        />
      </div>
    </Card>
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
