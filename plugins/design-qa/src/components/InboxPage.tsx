import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { StatusBadge } from "@langchain/macaw-components/StatusBadge"
import { Switch } from "@langchain/macaw-components/Switch"
import { cn } from "@langchain/macaw-components/utils/cn"
import { useHostContext } from "@openswe/plugin-sdk/react"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { CaretLeftIcon } from "@phosphor-icons/react/dist/ssr/CaretLeft"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { Assessment } from "../lib/assessment"
import type { FrontendReviewRun } from "../lib/capture"
import { runOverview } from "../lib/coverage"
import { useImage, useNearViewport } from "../lib/media"
import { splitRepository } from "../lib/pullRequest"
import {
  DesignQa,
  errorMessage,
  isForbidden,
  useInboxChanges,
  useInboxPage,
  useInboxPreferences,
  useInboxSearch,
  useRepositoryCaptures,
  useMedia,
  useRepositories,
  useShareRepositories,
  useTitle,
  type InboxItem,
  type RunMedia,
} from "../lib/queries"
import { InboxOrder, PAGE_SIZES, SORTS } from "../lib/inboxOrder"
import type { InboxRoute, InboxScope } from "../lib/route"
import { RouteLink, useRouter } from "../lib/router"
import { OpenPullRequestInput } from "./OpenPullRequestInput"
import { ShareRepositories } from "./ShareRepositories"
import { CaptureAction } from "./CaptureAction"
import { UpdateBanner } from "./UpdateBanner"

/** Open pull requests and their capture status, as cards. */
export function InboxPage() {
  useTitle(null)
  const repositories = useRepositories()
  const share = useShareRepositories()
  const { route, inbox, navigate } = useRouter()
  const current = route.page === "inbox" ? route : inbox
  const shared = repositories.data?.map((one) => one.fullName) ?? []
  useInboxChanges(shared)

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-space-5 px-space-5 py-space-6 sm:px-space-6">
      <UpdateBanner />
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
              value={current.scope}
              onChange={(scope) => navigate({ page: "inbox", scope, index: 0 })}
              options={[
                {
                  value: "review-requested",
                  display: "Requested from you",
                  "aria-label": `Requested from you${current.scope === "review-requested" ? ", selected" : ""}`,
                },
                {
                  value: "mine",
                  display: "My PRs",
                  "aria-label": `My PRs${current.scope === "mine" ? ", selected" : ""}`,
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
          <PullRequests key={current.scope} route={current} />
        </>
      )}
    </main>
  )
}

function PullRequests({ route }: { route: InboxRoute }) {
  const { scope } = route
  const { user } = useHostContext()
  const { navigate } = useRouter()
  const queryClient = useQueryClient()
  const search = useInboxSearch(scope)
  const [text, setText] = useState("")
  const [showSkipped, setShowSkipped] = useState(false)
  const { preferences, change } = useInboxPreferences()
  const { pageSize, sort } = preferences
  const pulls = search.data?.pulls ?? []
  const captures = useRepositoryCaptures([
    ...new Set(pulls.map(({ repository }) => repository)),
  ])
  const skipped = pulls.filter(({ repository, number }) =>
    DesignQa.skipped(captures.byRepository.get(repository), number)
  )
  const filter = text.trim().toLowerCase()
  const matching = pulls.filter(
    (pull) =>
      (showSkipped || !skipped.includes(pull)) &&
      `${pull.repository} #${pull.number} ${pull.title}`
        .toLowerCase()
        .includes(filter)
  )
  const ordered = InboxOrder.sorted(
    matching,
    ({ repository, number }) =>
      captures.byRepository.get(repository)?.runs.get(number),
    sort
  )
  const pageCount = Math.max(1, Math.ceil(ordered.length / pageSize))
  const index = Math.min(route.index, pageCount - 1)
  const first = index * pageSize
  const shown = ordered.slice(first, first + pageSize)
  const page = useInboxPage(scope, shown)
  if (isForbidden(search.error) || isForbidden(page.error))
    return <ShareRepositories unshared />

  const items = shown.length > 0 ? (page.data?.items ?? []) : []
  const loading =
    search.isPending || captures.pending || (shown.length > 0 && page.isPending)
  const fetching = search.isFetching || page.isFetching
  const failed = search.error ?? captures.error ?? page.error
  const turnTo = (next: number) => navigate({ ...route, index: next })

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
          aria-label="Filter pull requests"
          placeholder="Filter by title, repo, or number…"
          debounceMs={0}
          value={text}
          onChange={(next) => {
            setText(next)
            if (route.index !== 0) turnTo(0)
          }}
        />
        <Select
          size="sm"
          aria-label="Sort pull requests"
          triggerClassName="w-auto"
          value={sort}
          options={[...SORTS]}
          renderValue={(option) => `Sort: ${option.label ?? option.value}`}
          onChange={(next) => {
            if (!next) return
            change({ sort: next })
            if (route.index !== 0) turnTo(0)
          }}
        />
        <span className="text-xs text-secondary" aria-live="polite">
          {search.isPending
            ? "Loading pull requests…"
            : `${matching.length} PR${matching.length === 1 ? "" : "s"}${filter ? " match" : ""}`}
        </span>
        {skipped.length > 0 && (
          <Switch
            size="sm"
            checked={showSkipped}
            onChange={(next) => {
              setShowSkipped(next)
              if (route.index !== 0) turnTo(0)
            }}
            label={`Show ${skipped.length} skipped`}
          />
        )}
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          disabled={fetching}
          onClick={() => {
            void queryClient.invalidateQueries({ queryKey: ["inbox"] })
          }}
        >
          {fetching ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {failed && (
        <Card
          role="alert"
          className="mb-space-4 p-space-4 text-sm text-error-secondary"
        >
          {items.length > 0
            ? "Could not refresh every PR. Showing the loaded results. "
            : "Could not load pull requests. "}
          {errorMessage(failed)}
        </Card>
      )}
      {(search.data?.incomplete || page.data?.incomplete) && (
        <p role="status" className="mb-space-4 text-sm text-warning-secondary">
          GitHub returned a partial list. Some pull requests may be missing;
          refresh to try again.
        </p>
      )}
      {loading ? (
        <div
          aria-label="Loading pull requests"
          className="grid gap-space-4 sm:grid-cols-2 xl:grid-cols-3"
        >
          {Array.from({ length: Math.min(shown.length || 6, 6) }, (_, slot) => (
            <Skeleton key={slot} className="h-64 w-full" />
          ))}
        </div>
      ) : shown.length === 0 && !failed ? (
        <Card className="flex flex-col items-center gap-space-2 p-space-8 text-center">
          <EyeIcon
            size={28}
            className="text-icon-secondary"
            aria-hidden="true"
          />
          <h2 className="text-sm font-medium text-primary">
            {filter
              ? "No PRs match your filter"
              : skipped.length > 0 && !showSkipped
                ? "Nothing to check in Design QA"
                : scope === "mine"
                  ? "No open PRs in your shared repositories"
                  : "No open PRs requesting your review"}
          </h2>
          <p className="max-w-sm text-sm text-secondary">
            {filter
              ? "Try a different title, repository, or PR number."
              : skipped.length > 0 && !showSkipped
                ? `Design QA skipped ${skipped.length === 1 ? "the 1 open PR" : `all ${skipped.length} open PRs`} here, such as changes with no frontend files.`
                : scope === "mine"
                  ? "Your open PRs appear here, including those without captures. Open any PR above to view its QA page."
                  : "PRs appear here when you or a team you belong to are requested as reviewers. Open any PR above to view its QA page."}
          </p>
        </Card>
      ) : (
        <div
          aria-busy={page.isPlaceholderData}
          className={cn(
            "grid auto-rows-fr gap-space-4 transition-opacity sm:grid-cols-2 xl:grid-cols-3",
            page.isPlaceholderData && "opacity-60"
          )}
        >
          {items.map((item) => (
            <PullRequestCard
              key={`${item.repository}#${item.pull.number}`}
              item={item}
              login={user.login}
            />
          ))}
        </div>
      )}
      {ordered.length > PAGE_SIZES[0] && (
        <nav
          aria-label="Pull request pages"
          className="mt-space-4 flex flex-wrap items-center justify-between gap-space-3"
        >
          <div className="flex items-center gap-space-3 text-xs text-secondary">
            <span>
              {first + 1}–{first + shown.length} of {ordered.length}
            </span>
            <label className="flex items-center gap-space-2">
              Per page
              <Select
                size="sm"
                aria-label="Pull requests per page"
                triggerClassName="w-auto"
                value={String(pageSize)}
                options={PAGE_SIZES.map((size) => ({ value: String(size) }))}
                onChange={(next) => {
                  if (!next) return
                  change({ pageSize: Number(next) })
                  if (route.index !== 0) turnTo(0)
                }}
              />
            </label>
          </div>
          <div
            className={cn(
              "flex items-center gap-space-2",
              pageCount < 2 && "hidden"
            )}
          >
            <Button
              size="sm"
              color="secondary"
              variant="outlined"
              leftDecorator={CaretLeftIcon}
              disabled={index === 0}
              onClick={() => turnTo(index - 1)}
            >
              Previous
            </Button>
            <span className="text-xs text-secondary tabular-nums">
              Page {index + 1} of {pageCount}
            </span>
            <Button
              size="sm"
              color="secondary"
              variant="outlined"
              rightDecorator={CaretRightIcon}
              disabled={index >= pageCount - 1}
              onClick={() => turnTo(index + 1)}
            >
              Next
            </Button>
          </div>
        </nav>
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
  const report = !stale && run?.status === "complete" ? run.report : null
  const reviewed = report?.ai_review_status === "complete"
  const risk = reviewed && report.risk ? Assessment.risk(report.risk) : null
  const designSystem =
    reviewed && report.design_system
      ? Assessment.designSystem(report.design_system)
      : null
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
    run?.report?.risk?.reason ||
    Assessment.summary(run?.report?.summary ?? "")
      .flatMap((block) => (block.kind === "text" ? [block.text] : block.items))
      .join(" · ") ||
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
          <div className="flex flex-wrap items-center gap-space-2">
            <Badge color={status.color} size="sm">
              {status.label}
            </Badge>
            {risk && (
              <StatusBadge variant={risk.tone} size="sm">
                {risk.label}
              </StatusBadge>
            )}
            {designSystem && (
              <StatusBadge variant={designSystem.tone} size="sm">
                {designSystem.label}
              </StatusBadge>
            )}
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
