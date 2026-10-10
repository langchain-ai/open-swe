import {
  CaretRightIcon,
  CheckCircleFillIcon,
} from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise"
import { ChatCircleTextIcon } from "@phosphor-icons/react/dist/ssr/ChatCircleText"
import { CircleDashedIcon } from "@phosphor-icons/react/dist/ssr/CircleDashed"
import { GitMergeIcon } from "@phosphor-icons/react/dist/ssr/GitMerge"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"
import { XCircleIcon } from "@phosphor-icons/react/dist/ssr/XCircle"

import type {
  OpenPullRequest,
  ReviewCheckRun,
  ReviewDetail,
  ReviewFinding,
} from "@/lib/api"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"
import { subtleLink } from "@/features/reviews/lib/styles"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { MarkPullRequestReady } from "@/features/reviews/components/MarkPullRequestReady"
import { MergePullRequest } from "@/features/reviews/components/MergePullRequest"
import { PullRequestThreadAction } from "@/features/reviews/components/PullRequestThreadAction"
import { RequestHumanReview } from "@/features/reviews/components/RequestHumanReview"
import { ReviewAssessmentCard } from "@/features/reviews/components/ReviewAssessmentCard"
import {
  ReviewerList,
  reviewerRows,
} from "@/features/reviews/components/ReviewerList"
import { UpdatePullRequestBranch } from "@/features/reviews/components/UpdatePullRequestBranch"
import {
  canAttemptMerge,
  canUpdateBranch,
  hasUnresolvedConversations,
  isConflicted,
} from "@/features/reviews/lib/status"
import { AgentMark } from "./AgentMark"
import {
  askAboutFinding,
  findingGroupColor,
  findingGroupLabel,
  findingLocation,
  findingGroupTextColor,
  findingTarget,
  fixFinding,
  isAnchored,
  openFindingCounts,
  rankFindings,
} from "./findings"
import { InlineCode } from "./inlineCode"
import {
  reviewQueries,
  useOpenConversations,
  useRefreshPullRequest,
  useReviewStatus,
  type PullRequestRef,
} from "./queries"
import {
  isLive,
  pullRequestStanding,
  type Standing,
  type StandingTone,
} from "./pullRequestStanding"
import { useReviewPage } from "./store"
import { plural } from "./text"

const toneColor: Record<StandingTone, string> = {
  ready: "var(--icon-success)",
  blocked: "var(--icon-error)",
  waiting: "var(--icon-warning)",
  merged: "var(--text-purple)",
  closed: "var(--icon-tertiary)",
  unknown: "var(--border-default)",
}

/** The PR's standing sentence and what feeds it; the card and the header read the same one. */
export function usePullRequestStanding(pr: PullRequestRef) {
  const session = useSession()
  const detail = useQuery(reviewQueries.detail(pr)).data
  const statusQuery = useReviewStatus(pr)
  const open = useOpenConversations(pr)
  if (!detail) return null
  // Undefined while loading; null once GitHub couldn't say.
  const status = statusQuery.isError ? null : statusQuery.data
  return {
    detail,
    status,
    login: session.data?.login ?? "",
    standing: pullRequestStanding(detail, status, session.data?.login, open),
  }
}

/** The tone of where the PR stands, as a dot with a soft halo. */
export function StandingDot({
  tone,
  className,
}: {
  tone: StandingTone
  className?: string
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "size-2.5 shrink-0 rounded-full",
        tone === "waiting" && "animate-status-pulse",
        className
      )}
      style={{
        background: toneColor[tone],
        boxShadow: `0 0 0 4px color-mix(in oklab, ${toneColor[tone]} 18%, transparent)`,
      }}
    />
  )
}

/** Where the PR stands, in one sentence, with the single next step beside it. */
export function StandingPanel({ pr }: { pr: PullRequestRef }) {
  const current = usePullRequestStanding(pr)
  const showFindings = useReviewPage((state) => state.showFindings)
  const showOpenConversations = useReviewPage(
    (state) => state.showOpenConversations
  )
  const setStandingInView = useReviewPage((state) => state.setStandingInView)
  const openReview = useReviewPage((state) => state.openReview)
  // Watching the card itself, so the header knows when to carry its sentence.
  const ref = useRef<HTMLElement>(null)
  const ready = current !== null
  useEffect(() => {
    const node = ref.current
    if (!node) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry) setStandingInView(entry.isIntersecting)
    })
    observer.observe(node)
    return () => {
      observer.disconnect()
      setStandingInView(false)
    }
  }, [setStandingInView, ready])
  if (!current) return null
  const { detail, status, login, standing } = current
  const open = isLive(detail)

  return (
    <section
      ref={ref}
      aria-label="Pull request status"
      className="overflow-hidden rounded-xl border border-default bg-surface-level-1"
    >
      <div className="flex flex-wrap items-center gap-x-space-4 gap-y-space-2 px-space-4 py-space-4">
        <div className="flex min-w-0 flex-1 items-start gap-space-3">
          <StandingDot tone={standing.tone} className="mt-[7px]" />
          <div className="min-w-0">
            <p className="text-lg leading-6 font-semibold tracking-[-0.015em] text-primary">
              {standing.headline}
            </p>
            {standing.details.length > 0 && (
              <p className="mt-0.5 flex flex-wrap gap-x-space-3 text-xxs leading-5">
                {standing.details.map((fact) => (
                  <button
                    key={fact.text}
                    type="button"
                    onClick={
                      fact.opens === "findings"
                        ? showFindings
                        : showOpenConversations
                    }
                    className={subtleLink}
                  >
                    {fact.text}
                  </button>
                ))}
              </p>
            )}
          </div>
        </div>
        {/* Every other next step is a button in its row below. */}
        {standing.next?.kind === "review" && (
          <Button size="md" onClick={() => openReview()}>
            Review changes
          </Button>
        )}
      </div>
      {open && status && (
        <div className="divide-y border-t border-default">
          <ChecksRow checks={detail.checks} status={status} login={login} />
          <ReviewsRow pr={pr} detail={detail} status={status} login={login} />
          <BranchRow pr={pr} detail={detail} status={status} login={login} />
          <OpenSweRow pr={pr} detail={detail} />
          {canAttemptMerge(status) && (
            <MergeRow pr={pr} status={status} baseRef={detail.pr.base_ref} />
          )}
        </div>
      )}
      {(!open || !status) && (
        <div className="divide-y border-t border-default">
          {open &&
            status === undefined &&
            ["Checks", "Reviews"].map((label) => (
              <Row
                key={label}
                icon={
                  <span className="size-3.5 rounded-full bg-surface-level-3" />
                }
                label={label}
              >
                <Skeleton className="h-3 w-40" />
              </Row>
            ))}
          <OpenSweRow pr={pr} detail={detail} />
        </div>
      )}
    </section>
  )
}

/** The one thing to do next, carried in the header; its own Review button covers a review. */
export function NextStepAction({
  pr,
  standing,
  status,
  login,
}: {
  pr: PullRequestRef
  standing: Standing
  status: OpenPullRequest
  login: string
}) {
  const refresh = useRefreshPullRequest(pr)
  const next = standing.next
  if (!next) return null
  switch (next.kind) {
    case "review":
      return null
    case "agent":
      return (
        <PullRequestThreadAction
          pr={status}
          login={login}
          actions={[next.action]}
        />
      )
    case "mark-ready":
      return <MarkPullRequestReady pr={status} onReady={refresh.status} />
    case "request-review":
      return <RequestHumanReview pr={status} />
    case "update-branch":
      return <UpdatePullRequestBranch pr={status} onUpdated={refresh.status} />
    case "merge":
      return <MergePullRequest pr={status} onMerged={refresh.merged} />
  }
}

function Row({
  icon,
  label,
  children,
  action,
  expandable,
  expanded,
  onToggle,
}: {
  icon: ReactNode
  label: string
  children: ReactNode
  action?: ReactNode
  expandable?: boolean
  expanded?: boolean
  onToggle?: () => void
}) {
  const body = (
    <>
      <span className="flex size-4 shrink-0 items-center justify-center">
        {icon}
      </span>
      <span className="w-[5.5rem] shrink-0 text-xs font-medium text-primary">
        {label}
      </span>
      <span className="min-w-0 flex-1 truncate text-xs text-secondary">
        {children}
      </span>
      {expandable && (
        <CaretRightIcon
          className={cn(
            "size-3 shrink-0 text-secondary transition-transform",
            expanded && "rotate-90"
          )}
        />
      )}
    </>
  )
  return (
    <div className="flex min-h-10 items-center gap-space-3 px-space-4 py-space-1">
      {expandable ? (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={onToggle}
          className="-my-space-1 flex min-w-0 flex-1 items-center gap-space-3 self-stretch py-space-1 text-left"
        >
          {body}
        </button>
      ) : (
        <div className="flex min-w-0 flex-1 items-center gap-space-3">
          {body}
        </div>
      )}
      {action && (
        <div className="flex shrink-0 items-center gap-space-2">{action}</div>
      )}
    </div>
  )
}

const passIcon = <CheckCircleFillIcon size={16} className="text-icon-success" />
const failIcon = (
  <XCircleIcon size={16} weight="fill" className="text-icon-error" />
)
const warnIcon = (
  <WarningCircleIcon size={16} weight="fill" className="text-icon-warning" />
)
const pendingIcon = (
  <CircleDashedIcon
    size={16}
    weight="regular"
    className="animate-spin text-icon-warning [animation-duration:3s]"
  />
)

function checkState(
  check: ReviewCheckRun
): "fail" | "pending" | "pass" | "skip" {
  if (check.status !== "completed") return "pending"
  if (
    [
      "failure",
      "timed_out",
      "cancelled",
      "action_required",
      "startup_failure",
    ].includes(check.conclusion ?? "")
  )
    return "fail"
  if (["skipped", "neutral", "stale"].includes(check.conclusion ?? ""))
    return "skip"
  return "pass"
}

const checkRank = { fail: 0, pending: 1, pass: 2, skip: 3 } as const

function ChecksRow({
  checks,
  status,
  login,
}: {
  checks: Array<ReviewCheckRun>
  status: OpenPullRequest
  login: string
}) {
  const [expanded, setExpanded] = useState(status.ci === "failing")
  const askInChat = useReviewPage((state) => state.askInChat)
  const states = checks.map(checkState)
  const failed = states.filter((s) => s === "fail").length
  const pending = states.filter((s) => s === "pending").length
  const passed = states.filter((s) => s === "pass").length
  const summary =
    checks.length === 0
      ? status.ci === "none"
        ? "No checks on this branch"
        : "Waiting for checks to report"
      : [
          failed && `${failed} failing`,
          pending && `${pending} running`,
          passed && `${passed} passed`,
        ]
          .filter(Boolean)
          .join(" · ")
  const sorted = checks
    .map((check, i) => ({ check, state: states[i]! }))
    .sort(
      (a, b) =>
        checkRank[a.state] - checkRank[b.state] ||
        a.check.name.localeCompare(b.check.name, undefined, { numeric: true })
    )
  return (
    <div>
      <Row
        icon={
          failed
            ? failIcon
            : pending || status.ci === "pending"
              ? pendingIcon
              : passIcon
        }
        label="Checks"
        expandable={checks.length > 0}
        expanded={expanded}
        onToggle={() => setExpanded((value) => !value)}
        action={
          status.ci === "failing" ? (
            <PullRequestThreadAction
              pr={status}
              login={login}
              actions={["fix-checks"]}
            />
          ) : null
        }
      >
        {summary}
      </Row>
      {expanded && (
        <ul className="max-h-64 overflow-y-auto pr-space-4 pb-space-2 pl-space-7">
          {sorted.map(({ check, state }) => (
            <li
              key={check.name}
              className="group flex h-7 items-center gap-space-2 text-xs"
            >
              {state === "fail" ? (
                <XCircleIcon
                  size={14}
                  weight="fill"
                  className="text-icon-error"
                />
              ) : state === "pending" ? (
                <CircleDashedIcon
                  size={14}
                  weight="regular"
                  className="text-icon-warning"
                />
              ) : (
                <CheckCircleFillIcon
                  size={14}
                  className={
                    state === "skip"
                      ? "text-icon-tertiary"
                      : "text-icon-success"
                  }
                />
              )}
              {check.url ? (
                <a
                  href={check.url}
                  target="_blank"
                  rel="noreferrer"
                  className="min-w-0 truncate text-primary hover:underline"
                >
                  {check.name}
                </a>
              ) : (
                <span className="min-w-0 truncate">{check.name}</span>
              )}
              {state === "fail" && (
                <button
                  type="button"
                  onClick={() =>
                    askInChat(
                      `Why is the \`${check.name}\` check failing on this pull request?`
                    )
                  }
                  className="ml-auto text-secondary opacity-0 group-hover:opacity-100 hover:text-primary focus-visible:opacity-100"
                >
                  Ask why
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ReviewsRow({
  pr,
  detail,
  status,
  login,
}: {
  pr: PullRequestRef
  detail: ReviewDetail
  status: OpenPullRequest
  login: string
}) {
  const showOpenConversations = useReviewPage(
    (state) => state.showOpenConversations
  )
  const [expanded, setExpanded] = useState(false)
  const open = useOpenConversations(pr)
  const reviewers = reviewerRows(
    status.reviewers ?? [],
    detail.pr.requested_reviewers,
    detail.pr.author?.login
  )
  const decision =
    status.reviewDecision === "approved"
      ? "Approved"
      : status.reviewDecision === "changes_requested"
        ? "Changes requested"
        : status.reviewRequired
          ? "An approving review is required"
          : "No reviews required"
  const unresolved = open
    ? open.threads.length
    : (status.unresolvedThreads ?? 0)
  return (
    <div>
      <Row
        expandable={reviewers.length > 0}
        expanded={expanded}
        onToggle={() => setExpanded((value) => !value)}
        icon={
          status.reviewDecision === "approved"
            ? passIcon
            : status.reviewDecision === "changes_requested"
              ? failIcon
              : status.reviewRequired
                ? warnIcon
                : passIcon
        }
        label="Reviews"
        action={
          <>
            {unresolved > 0 && (
              <button
                type="button"
                onClick={showOpenConversations}
                className={cn("text-xs", subtleLink)}
              >
                {unresolved} unresolved
              </button>
            )}
            {hasUnresolvedConversations(status) &&
              (open ? open.current > 0 : unresolved > 0) && (
                <PullRequestThreadAction
                  pr={status}
                  login={login}
                  actions={["address-comments"]}
                />
              )}
            {status.draft === false && <RequestHumanReview pr={status} />}
          </>
        }
      >
        {decision}
      </Row>
      {expanded && (
        <ReviewerList
          rows={reviewers}
          className="pr-space-4 pb-space-3 pl-space-7"
        />
      )}
    </div>
  )
}

function BranchRow({
  pr,
  detail,
  status,
  login,
}: {
  pr: PullRequestRef
  detail: ReviewDetail
  status: OpenPullRequest
  login: string
}) {
  const refresh = useRefreshPullRequest(pr)
  // Each is its own row, so a conflicted draft still offers to mark it ready.
  return (
    <>
      {isConflicted(status) && (
        <Row
          icon={failIcon}
          label="Branch"
          action={
            <PullRequestThreadAction
              pr={status}
              login={login}
              actions={["fix-conflicts"]}
            />
          }
        >
          Conflicts with {detail.pr.base_ref} must be resolved
        </Row>
      )}
      {status.mergeState === "behind" && canUpdateBranch(status) && (
        <Row
          icon={warnIcon}
          label="Branch"
          action={
            <UpdatePullRequestBranch pr={status} onUpdated={refresh.status} />
          }
        >
          Behind {detail.pr.base_ref}
        </Row>
      )}
      {status.draft && (
        <Row
          icon={warnIcon}
          label="Draft"
          action={<MarkPullRequestReady pr={status} onReady={refresh.status} />}
        >
          Reviewers aren&apos;t notified yet
        </Row>
      )}
    </>
  )
}

function MergeRow({
  pr,
  status,
  baseRef,
}: {
  pr: PullRequestRef
  status: OpenPullRequest
  baseRef: string
}) {
  const refresh = useRefreshPullRequest(pr)
  const [merged, setMerged] = useState(false)
  return (
    <Row
      icon={
        <GitMergeIcon size={16} weight="bold" className="text-icon-success" />
      }
      label="Merge"
      action={
        <MergePullRequest
          pr={status}
          apply={() => {
            setMerged(true)
            return () => setMerged(false)
          }}
          onMerged={refresh.merged}
        />
      }
    >
      {merged
        ? "Merging…"
        : status.mergeState === "unstable"
          ? `Into ${baseRef}, though some checks aren't passing`
          : `Into ${baseRef}`}
    </Row>
  )
}

function OpenSweRow({
  pr,
  detail,
}: {
  pr: PullRequestRef
  detail: ReviewDetail
}) {
  const queryClient = useQueryClient()
  const { bugs, flags } = openFindingCounts(detail.findings)
  // Open while there is something to look at, until the person folds it; a
  // link elsewhere that names the findings opens it again.
  const findingsKey = useReviewPage((state) => state.findingsKey)
  const [folded, setFolded] = useState<{ value: boolean; key: number } | null>(
    null
  )
  const expanded =
    folded === null || folded.key !== findingsKey
      ? bugs + flags > 0 || findingsKey > 0
      : !folded.value
  const setExpanded = (change: (value: boolean) => boolean) =>
    setFolded({ value: !change(expanded), key: findingsKey })
  const reReview = useMutation({
    mutationFn: () => api.reReview(pr.owner, pr.repo, pr.number),
    meta: { errorTitle: "Couldn't start the Open SWE review" },
    onSuccess: () => {
      queryClient.setQueryData(reviewQueries.detail(pr).queryKey, (old) =>
        old ? { ...old, status: "running" as const } : old
      )
      void queryClient.invalidateQueries({
        queryKey: reviewQueries.detail(pr).queryKey,
      })
    },
  })
  const running = detail.status === "running" || reReview.isPending
  const assessment = detail.assessment
  const canExpand = detail.findings.length > 0 || Boolean(assessment)
  // Open, the assessment card below says the risk itself.
  const verdict = assessment && !(expanded && canExpand) ? assessment : null
  const summary = running
    ? "Reviewing this pull request…"
    : detail.status === "none"
      ? "Not reviewed yet"
      : detail.status === "error"
        ? `Review failed${detail.review_error ? `: ${detail.review_error}` : ""}`
        : [
            verdict ? `Risk ${verdict.risk_score}/5` : null,
            verdict
              ? verdict.approved
                ? "approved"
                : verdict.decision === "would_approve"
                  ? "would approve"
                  : "needs human review"
              : null,
            bugs || flags
              ? [bugs && plural(bugs, "bug"), flags && plural(flags, "flag")]
                  .filter(Boolean)
                  .join(", ")
              : "no open findings",
          ]
            .filter(Boolean)
            .join(" · ")
  const prOpen = isLive(detail)
  return (
    <div>
      <Row
        icon={
          running ? <Spinner size="xs" /> : <AgentMark className="size-4" />
        }
        label="Open SWE"
        expandable={canExpand}
        expanded={expanded}
        onToggle={() => setExpanded((value) => !value)}
        action={
          <details className="group/actions">
            <summary className="flex cursor-pointer list-none items-center gap-space-1 rounded-sm text-xs text-secondary hover:text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 [&::-webkit-details-marker]:hidden">
              Review actions
              <CaretRightIcon className="size-3 group-open/actions:rotate-90" />
            </summary>
            <div className="flex flex-wrap items-center gap-space-3 pt-space-2">
              {detail.thread_id && (
                <Link
                  to="/agents/$threadId"
                  params={{ threadId: detail.thread_id }}
                  className={cn("text-xs", subtleLink)}
                >
                  {running ? "Watch it work" : "See how it reviewed"}
                </Link>
              )}
              {prOpen && (
                <Button
                  size="sm"
                  color="secondary"
                  variant="plain"
                  leftDecorator={ArrowClockwiseIcon}
                  disabled={running}
                  onClick={() => reReview.mutate()}
                >
                  {detail.status === "none" ? "Review" : "Re-review"}
                </Button>
              )}
            </div>
          </details>
        }
      >
        {summary}
      </Row>
      {expanded && canExpand && (
        <div
          // Remounting replays the flash each time something asks for the findings.
          key={findingsKey}
          className={cn(
            "pr-space-4 pb-space-3 pl-space-7",
            findingsKey > 0 && "motion-safe:animate-attention-flash"
          )}
        >
          {detail.findings.length > 0 && (
            <FindingQueue findings={detail.findings} />
          )}
          {assessment && (
            <div className="mt-space-2 [&>section]:mt-0 [&>section]:border-dashed [&>section]:bg-transparent [&>section]:p-space-3 [&>section]:text-xs">
              <ReviewAssessmentCard
                assessment={assessment}
                owner={pr.owner}
                repo={pr.repo}
                number={pr.number}
                headSha={detail.head_sha}
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FindingQueue({ findings }: { findings: Array<ReviewFinding> }) {
  const showFinding = useReviewPage((state) => state.showFinding)
  const askInChat = useReviewPage((state) => state.askInChat)
  const [openId, setOpenId] = useState<string | null>(null)
  return (
    <ol className="flex flex-col">
      {rankFindings(findings).map((finding) => {
        const settled = finding.status !== "open"
        const open = openId === finding.id
        return (
          <li
            key={finding.id}
            className={cn(
              "group relative border-l-2 py-space-1 pl-space-3",
              settled && "opacity-55"
            )}
            style={{ borderLeftColor: findingGroupColor[finding.group] }}
          >
            <div className="flex flex-wrap items-baseline gap-x-space-2 gap-y-0.5 text-xs">
              <span
                className="shrink-0 font-medium"
                style={{ color: findingGroupTextColor[finding.group] }}
              >
                {findingGroupLabel[finding.group]}
              </span>
              <button
                type="button"
                aria-expanded={open}
                onClick={() => setOpenId(open ? null : finding.id)}
                className={cn(
                  "min-w-[16ch] flex-1 text-left leading-5 text-primary hover:underline",
                  settled && "line-through decoration-text-tertiary"
                )}
              >
                <InlineCode text={finding.title} />
              </button>
              <span className="ml-auto flex shrink-0 items-baseline gap-space-1 text-xxs text-secondary">
                <button
                  type="button"
                  disabled={!isAnchored(finding)}
                  onClick={() => {
                    if (isAnchored(finding))
                      showFinding(finding.id, findingTarget(finding))
                  }}
                  className="rounded-sm px-space-1 py-0.5 font-mono hover:bg-surface-level-1-hover hover:text-primary disabled:hover:bg-transparent disabled:hover:text-secondary"
                  title={finding.file}
                >
                  {findingLocation(finding)}
                </button>
                <Button
                  size="xs"
                  variant="plain"
                  color="secondary"
                  aria-label={`Ask Open SWE about: ${finding.title}`}
                  onClick={() => askInChat(askAboutFinding(finding))}
                >
                  Ask
                </Button>
                {!settled && (
                  <Button
                    size="xs"
                    variant="plain"
                    color="secondary"
                    aria-label={`Ask Open SWE to fix: ${finding.title}`}
                    onClick={() => askInChat(fixFinding(finding))}
                  >
                    Fix it
                  </Button>
                )}
              </span>
            </div>
            {open && (
              <div className="markdown-body mt-space-1 max-w-prose text-xs text-secondary">
                <Markdown content={finding.description} />
                {finding.resolution_note && (
                  <p className="mt-space-1 flex items-center gap-space-1">
                    <ChatCircleTextIcon className="size-3" weight="regular" />
                    {finding.resolution_note}
                  </p>
                )}
              </div>
            )}
          </li>
        )
      })}
    </ol>
  )
}
