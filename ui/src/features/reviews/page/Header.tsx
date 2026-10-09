import { ArrowLeftIcon, ArrowUpIcon } from "@langchain/macaw-components/icons"
import { Badge, type BadgeProps } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ChatsCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatsCircle"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { GitMergeIcon } from "@phosphor-icons/react/dist/ssr/GitMerge"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { KeyboardIcon } from "@phosphor-icons/react/dist/ssr/Keyboard"
import { LinkIcon } from "@phosphor-icons/react/dist/ssr/Link"
import { TreeViewIcon } from "@phosphor-icons/react/dist/ssr/TreeView"
import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"

import type { ReviewDetail } from "@/lib/api"
import { cn, formatRelativeTime } from "@/lib/utils"
import { useAppCommandControls } from "@/lib/appCommands"
import { StatusPill } from "@/features/reviews/components/StatusPill"
import { SubmitReviewPopover } from "@/features/reviews/components/SubmitReviewPopover"
import { copyText } from "@/features/reviews/lib/copyText"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { statusLabels } from "@/features/reviews/lib/status"
import { ProfileLink } from "./notes/Byline"
import { reviewQueries, type PullRequestRef } from "./queries"
import { isLive } from "./pullRequestStanding"
import { NextStepAction, StandingDot, usePullRequestStanding } from "./Standing"
import { useReviewPage } from "./store"

type PillState = ReviewDetail["pr"]["state"]

const pillColor: Record<PillState, BadgeProps["color"]> = {
  open: "success",
  draft: "secondary",
  merged: "primary",
  closed: "error",
}

// The status is fresher than the detail once someone marks the PR ready.
function pillState(
  detail: ReviewDetail,
  draft: boolean | null | undefined
): PillState {
  if (!isLive(detail)) return detail.pr.state
  return (draft ?? detail.pr.state === "draft") ? "draft" : "open"
}

/** Two lines that never scroll away: what this PR is, and what you can do with it. */
export function Header({
  pr,
  leftInset,
  compactRail,
  navigatorShown,
}: {
  pr: PullRequestRef
  leftInset: string
  compactRail: boolean
  navigatorShown: boolean
}) {
  const detailQuery = useQuery(reviewQueries.detail(pr))
  const detail = detailQuery.data
  const diff = useQuery(reviewQueries.diff(pr)).data
  const viewedCount = useReviewPage(
    (state) =>
      diff?.files.filter((file) => state.viewed.has(file.path)).length ?? 0
  )
  const entryOrder = useReviewPage((state) => state.entryOrder)
  const reviewOpen = useReviewPage((state) => state.reviewOpen)
  const reviewVerdict = useReviewPage((state) => state.reviewVerdict)
  const setReviewOpen = useReviewPage((state) => state.setReviewOpen)
  const toggleNavigator = useReviewPage((state) => state.toggleNavigator)
  const setRailTab = useReviewPage((state) => state.setRailTab)
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const jumpToUnviewed = useReviewPage((state) => state.jumpToUnviewed)
  const { openShortcutReference } = useAppCommandControls()
  const githubUrl = githubUrls.pullRequest(pr)
  const current = usePullRequestStanding(pr)
  const status = current?.status
  const state = detail ? pillState(detail, status?.draft) : null
  const live = detail !== undefined && isLive(detail)
  const files = diff?.files ?? []
  const firstEntry = entryOrder[0]
  const standingInView = useReviewPage((page) => page.standingInView)
  // Once the status card scrolls away, the header carries its sentence and next step.
  const carrying = !standingInView && current !== null

  return (
    <header
      data-desktop-drag-region=""
      className={cn(
        // Top-aligned at 8px so the first row lines up with the app's floating sidebar button.
        "flex shrink-0 items-start gap-3 border-b border-default bg-surface-level-1 pt-2 pr-3 pb-2.5",
        leftInset
      )}
    >
      <IconButton
        asChild
        icon={ArrowLeftIcon}
        label="Back to reviews"
        size="md"
        color="secondary"
        variant="plain"
        className="max-sm:hidden"
      >
        <Link to="/agents/reviews" />
      </IconButton>
      <IconButton
        icon={TreeViewIcon}
        label={navigatorShown ? "Hide files" : "Show files"}
        aria-pressed={navigatorShown}
        size="md"
        color="secondary"
        variant="plain"
        onClick={toggleNavigator}
      />
      <div className="min-w-0 flex-1">
        <div className="flex min-h-7 min-w-0 items-center gap-2">
          {/* An open PR shows where it stands, in the PR list's own pills; GitHub's lifecycle only once it's settled. */}
          {live && status ? (
            <span className="flex shrink-0 items-center gap-1 max-sm:[&>*:not(:first-child)]:hidden">
              {statusLabels(status).map((label) => (
                <StatusPill key={label} status={label} size="xs" />
              ))}
            </span>
          ) : live && status === undefined ? (
            <Skeleton className="h-5 w-16 shrink-0 rounded-full" />
          ) : state ? (
            <Badge
              size="xs"
              color={pillColor[state]}
              leftDecorator={
                state === "merged" ? GitMergeIcon : GitPullRequestIcon
              }
              className="shrink-0 capitalize"
            >
              {state}
            </Badge>
          ) : (
            !detailQuery.isError && (
              <Skeleton className="h-5 w-14 shrink-0 rounded-full" />
            )
          )}
          <h1 className="min-w-0 truncate text-[15px] leading-6 font-semibold tracking-[-0.01em] max-sm:line-clamp-2 max-sm:text-[14px] max-sm:leading-5 max-sm:whitespace-normal">
            <a
              href={githubUrl}
              target="_blank"
              rel="noreferrer"
              className="hover:underline"
            >
              <GithubLogoIcon
                aria-label="GitHub"
                size={16}
                weight="regular"
                className="mr-1.5 inline align-[-3px] text-icon-secondary max-sm:hidden"
              />
              {detail?.pr.title ?? `${pr.owner}/${pr.repo}`}
              <span className="font-normal text-secondary"> #{pr.number}</span>
            </a>
          </h1>
        </div>
        {detail ? (
          // The meta line and the status sentence share one slot and cross-fade.
          <div className="mt-0.5 grid min-w-0 max-sm:hidden">
            <p
              inert={carrying}
              className={cn(
                "col-start-1 row-start-1 flex min-w-0 items-center gap-1.5 overflow-hidden text-xs text-secondary transition-[opacity,translate] duration-200 ease-out motion-reduce:transition-none",
                carrying && "-translate-y-1 opacity-0"
              )}
            >
              <a
                href={githubUrls.repo(pr)}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 hover:text-primary hover:underline max-lg:hidden"
              >
                {pr.owner}/{pr.repo}
              </a>
              <span aria-hidden className="max-lg:hidden">
                ·
              </span>
              <ProfileLink
                author={detail.pr.author}
                className="shrink-0 font-medium text-primary"
              />
              <span className="shrink-0">
                {state === "merged"
                  ? "merged into"
                  : state === "closed"
                    ? "wanted to merge into"
                    : "wants to merge into"}
              </span>
              <a
                href={githubUrls.branch(pr, detail.pr.base_ref)}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 rounded bg-surface-level-3 px-1 py-px font-mono text-[11px] hover:text-primary"
              >
                {detail.pr.base_ref}
              </a>
              <span className="shrink-0 max-xl:hidden">from</span>
              <button
                type="button"
                title={`Copy ${detail.pr.head_ref}`}
                onClick={() =>
                  copyText(detail.pr.head_ref, "Copied the branch name")
                }
                className="group/branch flex max-w-[32ch] min-w-[8ch] items-center gap-1 rounded bg-surface-level-3 px-1 py-px font-mono text-[11px] hover:text-primary max-xl:hidden"
              >
                <span className="truncate">{detail.pr.head_ref}</span>
                <CopyIcon className="size-3 shrink-0 opacity-0 group-hover/branch:opacity-100" />
              </button>
              <button
                type="button"
                title="Go to the changes"
                onClick={() =>
                  firstEntry && jumpTo({ kind: "entry", id: firstEntry.id })
                }
                className="hidden shrink-0 rounded px-0.5 font-mono tabular-nums hover:bg-surface-level-1-hover lg:inline"
              >
                <span className="text-success-secondary">
                  +{detail.pr.additions}
                </span>{" "}
                <span className="text-error-secondary">
                  −{detail.pr.deletions}
                </span>
              </button>
              {detail.pr.created_at && (
                <a
                  href={githubUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="hidden shrink-0 hover:text-primary hover:underline xl:inline"
                >
                  · opened{" "}
                  {formatRelativeTime(new Date(detail.pr.created_at).getTime())}
                </a>
              )}
            </p>
            {current && (
              <button
                type="button"
                inert={!carrying}
                onClick={() => jumpTo({ kind: "top" })}
                className={cn(
                  "group col-start-1 row-start-1 flex min-w-0 items-center gap-2 justify-self-start rounded px-0.5 text-left text-xs transition-[opacity,translate] duration-200 ease-out hover:bg-surface-level-1-hover motion-reduce:transition-none",
                  !carrying && "translate-y-1 opacity-0"
                )}
              >
                <StandingDot
                  tone={current.standing.tone}
                  className="size-2 animate-none shadow-none!"
                />
                <span className="truncate font-medium text-primary">
                  {current.standing.headline}
                </span>
                <span className="flex shrink-0 items-center gap-0.5 text-secondary group-hover:text-primary">
                  <ArrowUpIcon className="size-3" />
                  Back to top
                </span>
              </button>
            )}
          </div>
        ) : (
          <Skeleton className="mt-1 h-3.5 w-80 max-w-full max-sm:hidden" />
        )}
      </div>
      <div className="flex h-7 shrink-0 items-center gap-3">
        {carrying && current?.status && (
          <div className="max-lg:hidden">
            <NextStepAction
              pr={pr}
              standing={current.standing}
              status={current.status}
              login={current.login}
            />
          </div>
        )}
        {files.length > 0 && (
          <button
            type="button"
            onClick={jumpToUnviewed}
            title="Go to the next file you haven't viewed"
            className="hidden shrink-0 items-center gap-2 rounded-md px-2 py-1 text-xs text-secondary tabular-nums hover:bg-surface-level-1-hover hover:text-primary md:flex"
          >
            <ProgressRing value={viewedCount / files.length} />
            {viewedCount}/{files.length} viewed
          </button>
        )}
        {compactRail && (
          <Button
            size="sm"
            color="secondary"
            variant="outlined"
            leftDecorator={ChatsCircleIcon}
            aria-label="Chat"
            onClick={() => setRailTab("chat")}
          >
            <span className="max-sm:hidden">Chat</span>
          </Button>
        )}
        {detail && isLive(detail) && (
          <SubmitReviewPopover
            owner={pr.owner}
            repo={pr.repo}
            number={pr.number}
            open={reviewOpen}
            onOpenChange={setReviewOpen}
            defaultVerdict={reviewVerdict}
          />
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <IconButton
              icon={DotsThreeIcon}
              iconWeight="bold"
              label="More"
              size="md"
              color="secondary"
              variant="plain"
              tooltipProps={{ disabled: true }}
            />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuItem
              onSelect={() =>
                window.open(githubUrl, "_blank", "noopener,noreferrer")
              }
            >
              <GithubLogoIcon aria-hidden size={16} weight="regular" />
              Open on GitHub
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => copyText(window.location.href, "Copied the link")}
            >
              <LinkIcon aria-hidden size={16} weight="regular" />
              Copy link to this page
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={openShortcutReference}>
              <KeyboardIcon aria-hidden size={16} weight="regular" />
              Keyboard shortcuts
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  )
}

function ProgressRing({ value }: { value: number }) {
  const radius = 6
  const circumference = 2 * Math.PI * radius
  return (
    <svg viewBox="0 0 16 16" className="size-4 -rotate-90" aria-hidden>
      <circle
        cx="8"
        cy="8"
        r={radius}
        fill="none"
        stroke="var(--border-default)"
        strokeWidth="2"
      />
      <circle
        cx="8"
        cy="8"
        r={radius}
        fill="none"
        stroke="var(--icon-brand)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - value)}
        className="transition-[stroke-dashoffset] duration-300"
      />
    </svg>
  )
}
