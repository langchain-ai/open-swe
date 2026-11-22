import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { StatusBadge } from "@langchain/macaw-components/StatusBadge"
import { Tooltip } from "@langchain/macaw-components/Tooltip"
import { cn } from "@langchain/macaw-components/utils/cn"
import type { Person, ReviewOutcome } from "@openswe/plugin-sdk"
import { useHostContext } from "@openswe/plugin-sdk/react"
import { CaretLeftIcon } from "@phosphor-icons/react/dist/ssr/CaretLeft"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { PushPinIcon } from "@phosphor-icons/react/dist/ssr/PushPin"
import { UsersIcon } from "@phosphor-icons/react/dist/ssr/Users"
import { useQueryClient } from "@tanstack/react-query"
import { Fragment, useState } from "react"

import { Assessment } from "../lib/assessment"
import {
  CaptureAttempt,
  StoredCapture,
  type FrontendReviewRun,
  type FrontendScenario,
} from "../lib/capture"
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
  usePeople,
  usePrefetchInbox,
  useRepositoryCaptures,
  useMedia,
  useRepositories,
  useShareRepositories,
  useTitle,
  useVisibleCaptures,
  type InboxItem,
  type RunMedia,
} from "../lib/queries"
import { InboxOrder, PAGE_SIZES, SORTS } from "../lib/inboxOrder"
import type { InboxRoute, InboxScope } from "../lib/route"
import { RouteLink, useRouter } from "../lib/router"
import { Author } from "./Author"
import { OpenPullRequestInput } from "./OpenPullRequestInput"
import { PersonPicker } from "./PersonPicker"
import { ShareRepositories } from "./ShareRepositories"
import { CaptureAction } from "./CaptureAction"
import { PinButton } from "./PinButton"
import { UpdateBanner } from "./UpdateBanner"
import { useUsage, type UsageAction } from "../lib/usage"

/** Open pull requests and their capture status, as cards. */
export function InboxPage() {
  useTitle(null)
  const repositories = useRepositories()
  const share = useShareRepositories()
  const { route, inbox, navigate } = useRouter()
  const current = route.page === "inbox" ? route : inbox
  const shared = repositories.data?.map((one) => one.fullName) ?? []
  const record = useUsage()
  useInboxChanges(shared)
  usePrefetchInbox(current.scope)

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-space-5 px-space-5 py-space-6 sm:px-space-6">
      <UpdateBanner />
      <header className="flex flex-wrap items-center justify-between gap-space-3">
        <h1 className="text-2xl font-semibold tracking-tight text-primary">
          Design QA
        </h1>
        <div className="w-full max-w-sm min-w-0 sm:w-80">
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
              onChange={(scope) => {
                record(TAB_ACTIONS[scope])
                navigate({ page: "inbox", scope, index: 0 })
              }}
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
                {
                  value: "pinned",
                  display: "Pinned",
                  icon: PushPinIcon,
                  "aria-label": `Pinned${current.scope === "pinned" ? ", selected" : ""}`,
                },
                {
                  value: "people",
                  display: "People",
                  icon: UsersIcon,
                  "aria-label": `People${current.scope === "people" ? ", selected" : ""}`,
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
          <PullRequests key={current.scope} route={current} shared={shared} />
        </>
      )}
    </main>
  )
}

const TAB_ACTIONS = {
  "review-requested": "tab_requested",
  mine: "tab_mine",
  pinned: "tab_pinned",
  people: "tab_people",
} as const satisfies Record<InboxScope, UsageAction>

const DESCRIPTIONS: Record<InboxScope, string> = {
  "review-requested":
    "Open PRs with a GitHub review request for you or a team you belong to, in the repositories you shared. Comment mentions are not included.",
  mine: "Your open PRs and their capture status, in the repositories you shared.",
  pinned:
    "Your pins are private. PRs with skipped or unsuccessful captures, or only changes below 0.1%, are left out.",
  people:
    "Anyone's open PRs and their capture status, in the repositories you shared. Search by name or GitHub handle.",
}

const EMPTY: Record<InboxScope, { title: string; body: string }> = {
  "review-requested": {
    title: "No open PRs requesting your review",
    body: "PRs appear here when you or a team you belong to are requested as reviewers. Open any PR above to view its QA page.",
  },
  mine: {
    title: "No open PRs in your shared repositories",
    body: "Your open PRs appear here, including those without captures. Open any PR above to view its QA page.",
  },
  pinned: {
    title: "You don't have anything pinned yet! Get to pinning!",
    body: "Pin a PR from its card or its QA page to keep it here.",
  },
  people: {
    title: "No open PRs from them",
    body: "Their open PRs in the repositories you shared appear here, including those without captures.",
  },
}

function PullRequests({
  route,
  shared,
}: {
  route: InboxRoute
  shared: readonly string[]
}) {
  const { scope, person } = route
  const { navigate } = useRouter()
  const queryClient = useQueryClient()
  const search = useInboxSearch(scope, person)
  const choosing = scope === "people" && !person
  const [text, setText] = useState("")
  const { preferences, change } = useInboxPreferences()
  const record = useUsage()
  const { pageSize, sort } = preferences
  const readable = new Set(shared.map((name) => name.toLowerCase()))
  // A pin stays in storage while its repository isn't shared, out of the list.
  const pulls = (search.pulls ?? []).filter(
    ({ repository }) => scope !== "pinned" || readable.has(repository)
  )
  const captures = useRepositoryCaptures(shared)
  const hidden = pulls.filter(({ repository, number }) =>
    DesignQa.hidden(captures.byRepository.get(repository), number)
  )
  const filter = text.trim().toLowerCase()
  const matching = pulls.filter(
    (pull) =>
      !hidden.includes(pull) &&
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
  const index = search.more ? route.index : Math.min(route.index, pageCount - 1)
  const first = index * pageSize
  const shown = ordered.slice(first, first + pageSize)
  const visibleCaptures = useVisibleCaptures(captures.byRepository, shown)
  const page = useInboxPage(
    scope,
    shown,
    ordered.slice(first + pageSize, first + 2 * pageSize),
    visibleCaptures.byRepository
  )
  const items = page.items
  const people = usePeople(items.map(({ pull }) => pull.author))
  if (isForbidden(search.error)) return <ShareRepositories unshared />

  const loading =
    search.pulls === undefined
      ? !search.error
      : captures.pending ||
        visibleCaptures.pending ||
        (shown.length === 0 && search.more)
  const fetching = search.fetching || page.fetching
  const failed = search.error ?? captures.error
  const turnTo = (next: number) => navigate({ ...route, index: next })

  return (
    <section
      aria-label={
        scope === "mine"
          ? "My PRs for Design QA"
          : scope === "pinned"
            ? "Pinned PRs for Design QA"
            : scope === "people"
              ? "A person's PRs for Design QA"
              : "Requested PRs for Design QA"
      }
    >
      <p className="mb-space-3 text-xs text-secondary">{DESCRIPTIONS[scope]}</p>
      <div className="mb-space-4 flex flex-wrap items-center gap-space-3">
        {scope === "people" && (
          <PersonPicker
            selected={person}
            onPick={(login) => {
              if (login) record("person_picked")
              navigate({
                page: "inbox",
                scope: "people",
                index: 0,
                ...(login ? { person: login } : {}),
              })
            }}
          />
        )}
        {!choosing && (
          <>
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
                record("sort_changed")
                change({ sort: next })
                if (route.index !== 0) turnTo(0)
              }}
            />
            <span className="text-xs text-secondary" aria-live="polite">
              {search.pulls === undefined
                ? "Loading pull requests…"
                : `${matching.length}${search.more ? "+" : ""} candidate PR${matching.length === 1 && !search.more ? "" : "s"}${filter ? " match" : ""}${search.more ? " · loading more…" : ""}`}
            </span>
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
          </>
        )}
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
      {(search.incomplete || page.incomplete) && (
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
      ) : choosing ? (
        <Card className="flex flex-col items-center gap-space-2 p-space-8 text-center">
          <UsersIcon
            size={28}
            className="text-icon-secondary"
            aria-hidden="true"
          />
          <h2 className="text-sm font-medium text-primary">
            Pick someone to see their PRs
          </h2>
          <p className="max-w-sm text-sm text-secondary">
            Search for anyone who uses Robin by name or GitHub handle.
          </p>
        </Card>
      ) : items.length === 0 && !failed ? (
        <Card className="flex flex-col items-center gap-space-2 p-space-8 text-center">
          <EyeIcon
            size={28}
            className="text-icon-secondary"
            aria-hidden="true"
          />
          <h2 className="text-sm font-medium text-primary">
            {shown.length > 0
              ? "Nothing to check on this page"
              : filter
                ? "No PRs match your filter"
                : hidden.length > 0
                  ? "Nothing to check in Design QA"
                  : EMPTY[scope].title}
          </h2>
          <p className="max-w-sm text-sm text-secondary">
            {shown.length > 0
              ? "This page's captures are left out of Design QA, including changes below 0.1%. Try another page or open a PR above to request a new capture."
              : filter
                ? "Try a different title, repository, or PR number."
                : hidden.length > 0
                  ? "PRs with skipped or unsuccessful captures are left out. Open a PR above to request a new capture."
                  : EMPTY[scope].body}
          </p>
        </Card>
      ) : (
        <div
          aria-busy={search.remembered}
          className="grid auto-rows-fr gap-space-4 sm:grid-cols-2 xl:grid-cols-3"
        >
          {items.map((item) => (
            <PullRequestCard
              key={`${item.repository}#${item.pull.number}`}
              item={item}
              author={
                item.pull.author
                  ? people.get(item.pull.author.toLowerCase())
                  : undefined
              }
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
              {first + 1}–{first + shown.length} of {ordered.length} candidates
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
                  record("page_size_changed")
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

type Status = {
  label: string
  color: "primary" | "secondary" | "warning" | "error"
  /** Why, shown on hover: a capture attempt's reason. */
  reason?: string
}

/**
 * What a card says about its capture beyond the screenshot: a later attempt
 * that failed, and anything keeping its capture from being current and
 * reviewed. A ready capture says nothing.
 */
function cardStatuses(
  run: FrontendReviewRun | null,
  outcome: ReviewOutcome | null,
  stale: boolean
): Status[] {
  const attempt =
    outcome &&
    outcome.status !== "complete" &&
    (!run || Date.parse(outcome.created_at) > Date.parse(run.created_at))
      ? outcome
      : null
  const statuses: Status[] = attempt
    ? [
        {
          label: new CaptureAttempt(attempt).label,
          color: new CaptureAttempt(attempt).previewPending
            ? "primary"
            : attempt.status === "failed"
              ? "error"
              : attempt.status === "blocked"
                ? "warning"
                : "secondary",
          reason: attempt.reason,
        },
      ]
    : []
  if (!run)
    return statuses.length
      ? statuses
      : [{ label: "Not captured", color: "secondary" }]
  if (run.status === "pending")
    return [...statuses, { label: "Queued", color: "secondary" }]
  if (run.status === "running")
    return [...statuses, { label: "Capturing", color: "primary" }]
  if (run.status === "blocked" || run.status === "failed")
    return statuses.length
      ? statuses
      : [
          {
            label:
              run.status === "blocked"
                ? CaptureAttempt.isPreviewPending(run.error)
                  ? "Preview pending"
                  : "Capture blocked"
                : "Capture failed",
            color:
              run.status === "failed"
                ? "error"
                : CaptureAttempt.isPreviewPending(run.error)
                  ? "primary"
                  : "warning",
            reason: run.error ?? undefined,
          },
        ]
  if (stale) return [...statuses, { label: "Earlier commit", color: "warning" }]
  if (run.report?.ai_review_status === "running")
    return [...statuses, { label: "AI reviewing", color: "primary" }]
  if (run.report?.ai_review_status === "failed")
    return [...statuses, { label: "AI review failed", color: "warning" }]
  return statuses
}

function PullRequestCard({
  item,
  author,
}: {
  item: InboxItem
  author: Person | undefined
}) {
  const { pull: pr, run } = item
  const repository = splitRepository(item.repository)
  const stale = Boolean(
    run && "headSha" in pr && pr.headSha && run.head_sha !== pr.headSha
  )
  const statuses = cardStatuses(run, item.outcome, stale)
  const report = !stale && run?.status === "complete" ? run.report : null
  const reviewed = report?.ai_review_status === "complete"
  const risk = reviewed && report.risk ? Assessment.risk(report.risk) : null
  const designSystem =
    reviewed && report.design_system
      ? Assessment.designSystem(report.design_system)
      : null
  const closed =
    "state" in pr && pr.state !== "open"
      ? pr.merged
        ? "Merged"
        : "Closed"
      : null
  if (!repository) return null
  const ref = { ...repository, number: pr.number }

  return (
    <Card className="flex h-full min-w-0 flex-col overflow-hidden p-0">
      <RouteLink
        to={{ page: "pull", pr: ref, run: run?.id ?? null }}
        className="flex min-w-0 flex-1 flex-col rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-focus"
      >
        <div className="flex aspect-[16/6] items-center justify-center overflow-hidden border-b border-default bg-surface-level-3">
          <Thumbnail
            media={{
              repository: item.repository,
              pull: pr.number,
              run: run?.id ?? "",
            }}
            cover={run ? runOverview(run).cover : undefined}
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
            {(closed ?? pr.draft) && (
              <Badge color="secondary" size="xs">
                {closed ?? "Draft"}
              </Badge>
            )}
          </div>
          <h2 className="line-clamp-2 text-sm font-semibold text-primary">
            {pr.title}
          </h2>
          {statuses.length > 0 || risk || designSystem ? (
            <div className="flex flex-wrap items-center gap-space-2">
              {statuses.map((status) => (
                <Fragment key={status.label}>
                  <Tooltip title={status.reason}>
                    <Badge color={status.color} size="sm">
                      {status.label}
                    </Badge>
                  </Tooltip>
                  {status.reason && (
                    <span className="sr-only">{status.reason}</span>
                  )}
                </Fragment>
              ))}
              {risk && (
                <StatusBadge variant={risk.tone} size="sm">
                  {`Risk ${report?.risk?.score}/5`}
                </StatusBadge>
              )}
              {designSystem && (
                <StatusBadge variant={designSystem.tone} size="sm">
                  {designSystem.label}
                </StatusBadge>
              )}
            </div>
          ) : null}
          {(pr.author || run) && (
            <div className="mt-auto flex min-w-0 items-center justify-between gap-space-2 pt-space-1 text-xs text-secondary">
              {pr.author ? (
                <Author login={pr.author} person={author} />
              ) : (
                <span />
              )}
              {run && (
                <span className="shrink-0 text-tertiary">
                  Run {new Date(run.created_at).toLocaleDateString()}
                </span>
              )}
            </div>
          )}
        </div>
      </RouteLink>
      <div className="flex items-center justify-between gap-space-2 p-space-4 pt-0">
        <CaptureAction
          pr={ref}
          outcome={item.outcome}
          currentHeadSha={"headSha" in pr ? pr.headSha : undefined}
          hasCapture={Boolean(run)}
          run={run}
          disabled={closed !== null}
          showOutcome={false}
        />
        <PinButton pr={ref} title={pr.title} compact />
      </div>
    </Card>
  )
}

/**
 * The PR screenshot of the first paired state, in the dashboard's theme when
 * the review shot it in both, read once the card nears the screen.
 */
function Thumbnail({
  media,
  cover,
}: {
  media: RunMedia
  cover: FrontendScenario | undefined
}) {
  const { theme } = useHostContext()
  const [container, near] = useNearViewport<HTMLDivElement>()
  const name = cover ? StoredCapture.shots(cover, theme).after : null
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
        <EyeIcon size={28} className="text-icon-secondary" aria-hidden="true" />
      )}
    </div>
  )
}
