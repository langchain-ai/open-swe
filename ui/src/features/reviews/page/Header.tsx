import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import {
  ArrowLeftIcon,
  ChatsCircleIcon,
  CopyIcon,
  DotsThreeIcon,
  GitMergeIcon,
  GitPullRequestIcon,
  KeyboardIcon,
  LinkIcon,
  TreeViewIcon,
} from "@phosphor-icons/react"
import { IoLogoGithub } from "react-icons/io5"
import { toast } from "sonner"

import type { ReviewDetail } from "@/lib/api"
import { cn, formatRelativeTime } from "@/lib/utils"
import { useAppCommandControls } from "@/lib/appCommands"
import { Button } from "@/components/ui/button"
import {
  Menu,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
} from "@/components/ui/menu"
import { Skeleton } from "@/components/ui/skeleton"
import { SubmitReviewPopover } from "@/features/reviews/components/SubmitReviewPopover"
import { reviewQueries, type PullRequestRef } from "./queries"
import { NextStepAction, StandingDot, usePullRequestStanding } from "./Standing"
import { useReviewPage } from "./store"
import { statusLabels } from "@/features/reviews/lib/status"
import { StatusPill } from "@/features/reviews/components/StatusPill"

type PillState = "open" | "draft" | "merged" | "closed"

const pill: Record<PillState, { label: string; className: string }> = {
  open: { label: "open", className: "bg-success/15 text-success-foreground" },
  draft: { label: "draft", className: "bg-muted text-muted-foreground" },
  merged: { label: "merged", className: "bg-merged/15 text-merged-foreground" },
  closed: {
    label: "closed",
    className: "bg-destructive/12 text-destructive-foreground",
  },
}

function pillState(
  detail: ReviewDetail,
  draft: boolean | null | undefined
): PillState {
  if (detail.pr.merged_at) return "merged"
  if (detail.pr.state === "closed") return "closed"
  return draft ? "draft" : "open"
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
  const status = useQuery(reviewQueries.status(pr)).data
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
  const githubUrl = `https://github.com/${pr.owner}/${pr.repo}/pull/${pr.number}`
  const state = detail ? pillState(detail, status?.draft) : null
  const files = diff?.files ?? []
  const current = usePullRequestStanding(pr)
  const standingInView = useReviewPage((page) => page.standingInView)
  // Once the status card scrolls away, the header carries its sentence and next step.
  const carrying = !standingInView && current !== null

  return (
    <header
      data-desktop-drag-region=""
      className={cn(
        // Top-aligned at 8px so the first row lines up with the app's floating sidebar button.
        "flex shrink-0 items-start gap-3 border-b border-border bg-background pt-2 pr-3 pb-2.5",
        leftInset
      )}
    >
      <Link
        to="/agents/reviews"
        aria-label="Back to reviews"
        className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground max-sm:hidden"
      >
        <ArrowLeftIcon className="size-4" />
      </Link>
      <button
        type="button"
        aria-label={navigatorShown ? "Hide files" : "Show files"}
        aria-pressed={navigatorShown}
        onClick={toggleNavigator}
        className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        <TreeViewIcon className="size-4" />
      </button>
      <div className="min-w-0 flex-1">
        <div className="flex min-h-7 min-w-0 items-center gap-2">
          {/* An open PR shows where it stands, in the PR list's own pills; GitHub's lifecycle only once it's settled. */}
          {(state === "open" || state === "draft") && status ? (
            <span className="flex shrink-0 items-center gap-1 max-sm:[&>*:not(:first-child)]:hidden">
              {statusLabels(status).map((label) => (
                <StatusPill
                  key={label}
                  status={label}
                  className="h-5 py-0 text-[11px]"
                />
              ))}
            </span>
          ) : (state === "open" || state === "draft") && !status ? (
            <Skeleton className="h-5 w-16 shrink-0 rounded-full" />
          ) : state ? (
            <span
              className={cn(
                "inline-flex h-5 shrink-0 items-center gap-1 rounded-full px-2 text-[11px] font-medium capitalize max-sm:px-1.5",
                pill[state].className
              )}
            >
              {state === "merged" ? (
                <GitMergeIcon weight="bold" className="size-3" />
              ) : (
                <GitPullRequestIcon weight="bold" className="size-3" />
              )}
              <span className="max-sm:sr-only">{pill[state].label}</span>
            </span>
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
              <IoLogoGithub
                aria-label="GitHub"
                className="mr-1.5 inline size-4 align-[-3px] text-muted-foreground max-sm:hidden"
              />
              {detail?.pr.title ?? `${pr.owner}/${pr.repo}`}
              <span className="font-normal text-muted-foreground">
                {" "}
                #{pr.number}
              </span>
            </a>
          </h1>
        </div>
        {detail ? (
          // The meta line and the status sentence share one slot and cross-fade.
          <div className="mt-0.5 grid min-w-0 max-sm:hidden">
            <p
              inert={carrying}
              className={cn(
                "col-start-1 row-start-1 flex min-w-0 items-center gap-1.5 overflow-hidden text-xs text-muted-foreground transition-[opacity,translate] duration-200 ease-out motion-reduce:transition-none",
                carrying && "-translate-y-1 opacity-0"
              )}
            >
              <a
                href={`https://github.com/${pr.owner}/${pr.repo}`}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 hover:text-foreground hover:underline max-lg:hidden"
              >
                {pr.owner}/{pr.repo}
              </a>
              <span aria-hidden className="max-lg:hidden">
                ·
              </span>
              <a
                href={`https://github.com/${detail.pr.author?.login ?? ""}`}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 font-medium text-foreground/80 hover:text-foreground hover:underline"
              >
                {detail.pr.author?.login ?? "unknown"}
              </a>
              <span className="shrink-0">
                {state === "merged"
                  ? "merged into"
                  : state === "closed"
                    ? "wanted to merge into"
                    : "wants to merge into"}
              </span>
              <a
                href={`https://github.com/${pr.owner}/${pr.repo}/tree/${detail.pr.base_ref}`}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 rounded bg-muted px-1 py-px font-mono text-[11px] hover:text-foreground"
              >
                {detail.pr.base_ref}
              </a>
              <span className="shrink-0 max-xl:hidden">from</span>
              <button
                type="button"
                title={`Copy ${detail.pr.head_ref}`}
                onClick={() =>
                  void navigator.clipboard
                    .writeText(detail.pr.head_ref)
                    .then(() => toast.success("Copied the branch name"))
                }
                className="group/branch flex max-w-[32ch] min-w-[8ch] items-center gap-1 rounded bg-muted px-1 py-px font-mono text-[11px] hover:text-foreground max-xl:hidden"
              >
                <span className="truncate">{detail.pr.head_ref}</span>
                <CopyIcon className="size-3 shrink-0 opacity-0 group-hover/branch:opacity-100" />
              </button>
              <button
                type="button"
                title="Go to the changes"
                onClick={() =>
                  jumpTo({ kind: "entry", id: entryOrder[0]?.id ?? "" })
                }
                className="hidden shrink-0 rounded px-0.5 font-mono tabular-nums hover:bg-accent lg:inline"
              >
                <span className="text-success-foreground">
                  +{detail.pr.additions}
                </span>{" "}
                <span className="text-destructive-foreground">
                  −{detail.pr.deletions}
                </span>
              </button>
              {detail.pr.created_at && (
                <a
                  href={githubUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="hidden shrink-0 hover:text-foreground hover:underline xl:inline"
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
                title="Back to the status"
                className={cn(
                  "col-start-1 row-start-1 flex min-w-0 items-center gap-2 justify-self-start rounded px-0.5 text-left text-xs transition-[opacity,translate] duration-200 ease-out hover:bg-accent motion-reduce:transition-none",
                  !carrying && "translate-y-1 opacity-0"
                )}
              >
                <StandingDot
                  tone={current.standing.tone}
                  className="size-2 animate-none shadow-none!"
                />
                <span className="truncate font-medium text-foreground">
                  {current.standing.headline}
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
              inHeader
            />
          </div>
        )}
        {files.length > 0 && (
          <button
            type="button"
            onClick={jumpToUnviewed}
            title="Go to the next file you haven't viewed"
            className="hidden shrink-0 items-center gap-2 rounded-md px-2 py-1 text-xs text-muted-foreground tabular-nums hover:bg-accent hover:text-foreground md:flex"
          >
            <ProgressRing value={viewedCount / files.length} />
            {viewedCount}/{files.length} viewed
          </button>
        )}
        {compactRail && (
          <Button
            variant="outline"
            aria-label="Chat"
            onClick={() => setRailTab("chat")}
          >
            <ChatsCircleIcon />
            <span className="max-sm:hidden">Chat</span>
          </Button>
        )}
        {detail?.pr.state === "open" && (
          <SubmitReviewPopover
            owner={pr.owner}
            repo={pr.repo}
            number={pr.number}
            open={reviewOpen}
            onOpenChange={setReviewOpen}
            defaultVerdict={reviewVerdict}
          />
        )}
        <Menu>
          <MenuTrigger
            aria-label="More"
            render={
              <button
                type="button"
                className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
              />
            }
          >
            <DotsThreeIcon weight="bold" className="size-4" />
          </MenuTrigger>
          <MenuPopup align="end" className="w-56 p-1">
            <MenuItem
              onClick={() =>
                window.open(githubUrl, "_blank", "noopener,noreferrer")
              }
            >
              <IoLogoGithub /> Open on GitHub
            </MenuItem>
            <MenuItem
              onClick={() =>
                void navigator.clipboard
                  .writeText(window.location.href)
                  .then(() => toast.success("Copied the link"))
              }
            >
              <LinkIcon /> Copy link to this page
            </MenuItem>
            <MenuSeparator />
            <MenuItem onClick={openShortcutReference}>
              <KeyboardIcon /> Keyboard shortcuts
            </MenuItem>
          </MenuPopup>
        </Menu>
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
        stroke="var(--border)"
        strokeWidth="2"
      />
      <circle
        cx="8"
        cy="8"
        r={radius}
        fill="none"
        stroke="var(--primary)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - value)}
        className="transition-[stroke-dashoffset] duration-300"
      />
    </svg>
  )
}
