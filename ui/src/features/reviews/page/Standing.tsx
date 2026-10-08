import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import {
  ArrowClockwiseIcon,
  CaretRightIcon,
  ChatCircleTextIcon,
  CheckCircleIcon,
  CircleDashedIcon,
  GitMergeIcon,
  WarningCircleIcon,
  XCircleIcon,
} from "@phosphor-icons/react"

import type {
  OpenPullRequest,
  ReviewCheckRun,
  ReviewDetail,
  ReviewFinding,
} from "@/lib/api"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn, formatRelativeTime } from "@/lib/utils"
import type {
  ConversationAuthor,
  ConversationReview,
} from "@/features/reviews/lib/conversationApi"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { MarkPullRequestReady } from "@/features/reviews/components/MarkPullRequestReady"
import { MergePullRequest } from "@/features/reviews/components/MergePullRequest"
import { PullRequestThreadAction } from "@/features/reviews/components/PullRequestThreadAction"
import { RequestHumanReview } from "@/features/reviews/components/RequestHumanReview"
import { ReviewAssessmentCard } from "@/features/reviews/components/ReviewAssessmentCard"
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
  fixFinding,
  isAnchored,
  openConversationCounts,
  rankFindings,
} from "./findings"
import { InlineCode } from "./inlineCode"
import { reviewQueries, type PullRequestRef } from "./queries"
import {
  pullRequestStanding,
  type Standing,
  type StandingTone,
} from "./pullRequestStanding"
import { useReviewPage } from "./store"

const toneColor: Record<StandingTone, string> = {
  ready: "var(--success)",
  blocked: "var(--destructive)",
  waiting: "var(--warning)",
  merged: "var(--merged)",
  closed: "var(--muted-foreground)",
  unknown: "var(--border)",
}

/** The PR's standing sentence and what feeds it; the card and the header read the same one. */
export function usePullRequestStanding(pr: PullRequestRef) {
  const session = useSession()
  const detail = useQuery(reviewQueries.detail(pr)).data
  const status = useQuery(reviewQueries.status(pr)).data
  const threads = useQuery(reviewQueries.conversation(pr)).data?.threads
  if (!detail) return null
  return {
    detail,
    status,
    login: session.data?.login ?? "",
    standing: pullRequestStanding(detail, status, session.data?.login, threads),
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
  const open = detail.pr.state === "open"

  return (
    <section
      ref={ref}
      aria-label="Pull request status"
      className="overflow-hidden rounded-xl border border-border bg-card"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-4">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <StandingDot tone={standing.tone} className="mt-[7px]" />
          <div className="min-w-0">
            <p className="text-[17px] leading-6 font-semibold tracking-[-0.015em] text-foreground">
              {standing.headline}
            </p>
            {standing.details.length > 0 && (
              <p className="mt-0.5 flex flex-wrap gap-x-3 text-[12.5px] leading-5">
                {standing.details.map((fact) => (
                  <button
                    key={fact.text}
                    type="button"
                    onClick={
                      fact.opens === "findings"
                        ? showFindings
                        : showOpenConversations
                    }
                    className="text-muted-foreground underline decoration-muted-foreground/40 underline-offset-2 hover:text-foreground hover:decoration-foreground"
                  >
                    {fact.text}
                  </button>
                ))}
              </p>
            )}
          </div>
        </div>
        {status && (
          <NextStepAction
            pr={pr}
            standing={standing}
            status={status}
            login={login}
          />
        )}
      </div>
      {open && status && (
        <div className="divide-y divide-border border-t border-border">
          <ChecksRow checks={detail.checks} status={status} login={login} />
          <ReviewsRow detail={detail} status={status} login={login} />
          <BranchRow detail={detail} status={status} login={login} />
          <OpenSweRow pr={pr} detail={detail} />
          {canAttemptMerge(status) && (
            <MergeRow pr={pr} status={status} baseRef={detail.pr.base_ref} />
          )}
        </div>
      )}
      {(!open || !status) && (
        <div className="divide-y divide-border border-t border-border">
          {open &&
            ["Checks", "Reviews"].map((label) => (
              <Row
                key={label}
                icon={<span className="size-3.5 rounded-full bg-muted" />}
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

/**
 * The one thing to do next. In the card a merge lives in its own row, so it
 * appears here only `inHeader`, where the header's own Review button already
 * covers a review.
 */
export function NextStepAction({
  pr,
  standing,
  status,
  login,
  inHeader = false,
}: {
  pr: PullRequestRef
  standing: Standing
  status: OpenPullRequest
  login: string
  inHeader?: boolean
}) {
  const openReview = useReviewPage((state) => state.openReview)
  const queryClient = useQueryClient()
  const refresh = () =>
    void queryClient.invalidateQueries({ queryKey: ["review-page-pr"] })
  const next = standing.next
  if (!next) return null
  switch (next.kind) {
    case "review":
      return inHeader ? null : (
        <Button size="lg" onClick={() => openReview()}>
          Review changes
        </Button>
      )
    case "agent":
      return (
        <PullRequestThreadAction
          pr={status}
          login={login}
          action={next.action}
        />
      )
    case "mark-ready":
      return <MarkPullRequestReady pr={status} onReady={refresh} />
    case "request-review":
      return <RequestHumanReview pr={status} />
    case "update-branch":
      return <UpdatePullRequestBranch pr={status} onUpdated={refresh} />
    case "merge":
      return inHeader ? (
        <MergePullRequest
          pr={status}
          apply={() => () => {}}
          onMerged={() => {
            void queryClient.invalidateQueries({
              queryKey: reviewQueries.detail(pr).queryKey,
            })
            refresh()
          }}
        />
      ) : null
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
      <span className="w-[5.5rem] shrink-0 text-xs font-medium text-foreground">
        {label}
      </span>
      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
        {children}
      </span>
      {expandable && (
        <CaretRightIcon
          className={cn(
            "size-3 shrink-0 text-muted-foreground transition-transform",
            expanded && "rotate-90"
          )}
        />
      )}
    </>
  )
  return (
    <div className="flex min-h-10 items-center gap-3 px-4 py-1.5">
      {expandable ? (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={onToggle}
          className="-my-1.5 flex min-w-0 flex-1 items-center gap-3 self-stretch py-1.5 text-left"
        >
          {body}
        </button>
      ) : (
        <div className="flex min-w-0 flex-1 items-center gap-3">{body}</div>
      )}
      {action && (
        <div className="flex shrink-0 items-center gap-2">{action}</div>
      )}
    </div>
  )
}

const passIcon = (
  <CheckCircleIcon weight="fill" className="size-4 text-success" />
)
const failIcon = (
  <XCircleIcon weight="fill" className="size-4 text-destructive" />
)
const warnIcon = (
  <WarningCircleIcon weight="fill" className="size-4 text-warning" />
)
const pendingIcon = (
  <CircleDashedIcon className="size-4 animate-spin text-warning [animation-duration:3s]" />
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
              action="fix-checks"
            />
          ) : null
        }
      >
        {summary}
      </Row>
      {expanded && (
        <ul className="max-h-64 overflow-y-auto pr-4 pb-2 pl-11">
          {sorted.map(({ check, state }) => (
            <li
              key={check.name}
              className="group flex h-7 items-center gap-2 text-xs"
            >
              {state === "fail" ? (
                <XCircleIcon
                  weight="fill"
                  className="size-3.5 text-destructive"
                />
              ) : state === "pending" ? (
                <CircleDashedIcon className="size-3.5 text-warning" />
              ) : (
                <CheckCircleIcon
                  weight="fill"
                  className={cn(
                    "size-3.5",
                    state === "skip" ? "text-muted-foreground" : "text-success"
                  )}
                />
              )}
              {check.url ? (
                <a
                  href={check.url}
                  target="_blank"
                  rel="noreferrer"
                  className="min-w-0 truncate text-foreground/90 hover:underline"
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
                  className="ml-auto text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground focus-visible:opacity-100"
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
  detail,
  status,
  login,
}: {
  detail: ReviewDetail
  status: OpenPullRequest
  login: string
}) {
  const showOpenConversations = useReviewPage(
    (state) => state.showOpenConversations
  )
  const [expanded, setExpanded] = useState(false)
  const conversation = useQuery(
    reviewQueries.conversation({
      owner: detail.owner,
      repo: detail.repo,
      number: detail.number,
    })
  ).data
  // Each reviewer's standing review, as GitHub's sidebar lists them: a later
  // comment never replaces an approval or a change request, and the author
  // replying in threads is not reviewing.
  const author = detail.pr.author?.login.toLowerCase()
  const verdicts = new Map<string, ConversationReview>()
  for (const item of conversation?.items ?? []) {
    if (item.kind !== "review" || !item.author) continue
    if (item.author.login.toLowerCase() === author) continue
    const who = item.author.login.replace(/\[bot\]$/, "")
    const standing = verdicts.get(who)
    const decisive = (state: ConversationReview["state"]) =>
      state === "APPROVED" || state === "CHANGES_REQUESTED"
    if (item.state === "COMMENTED" && standing && decisive(standing.state))
      continue
    verdicts.set(who, item)
  }
  const conversations = openConversationCounts(
    conversation?.threads ?? [],
    detail.findings
  )
  const requested = detail.pr.requested_reviewers.map((r) => `@${r.login}`)
  const decision =
    status.reviewDecision === "approved"
      ? "Approved"
      : status.reviewDecision === "changes_requested"
        ? "Changes requested"
        : status.reviewRequired
          ? "An approving review is required"
          : "No reviews required"
  // Every unresolved thread, as GitHub and the discussion count them.
  const unresolved = conversation
    ? conversation.threads.filter((thread) => !thread.resolved).length
    : (status.unresolvedThreads ?? 0)
  return (
    <div>
      <Row
        expandable={verdicts.size > 0 || requested.length > 0}
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
                className="text-xs text-muted-foreground underline decoration-muted-foreground/40 underline-offset-2 hover:text-foreground"
              >
                {unresolved} unresolved
              </button>
            )}
            {hasUnresolvedConversations(status) &&
              (conversation ? conversations.current > 0 : unresolved > 0) && (
                <PullRequestThreadAction
                  pr={status}
                  login={login}
                  action="address-comments"
                />
              )}
            {status.draft === false && <RequestHumanReview pr={status} />}
          </>
        }
      >
        {decision}
      </Row>
      {expanded && (
        <ul className="flex flex-col gap-1 pr-4 pb-3 pl-11 text-xs">
          {[...verdicts.values()].map((review) => (
            <li key={review.id} className="flex items-center gap-2">
              <ReviewVerdictMark state={review.state} />
              <a
                href={profileUrl(review.author)}
                target="_blank"
                rel="noreferrer"
                className="font-medium hover:underline"
              >
                {review.author?.login.replace(/\[bot\]$/, "")}
              </a>
              <a
                href={review.html_url}
                target="_blank"
                rel="noreferrer"
                className="text-muted-foreground hover:text-foreground hover:underline"
              >
                {reviewVerdictWords[review.state]}{" "}
                {formatRelativeTime(new Date(review.created_at).getTime())}
              </a>
            </li>
          ))}
          {detail.pr.requested_reviewers.map((reviewer) => (
            <li key={reviewer.login} className="flex items-center gap-2">
              <CircleDashedIcon className="size-3.5 text-warning" />
              <a
                href={`https://github.com/${reviewer.login}`}
                target="_blank"
                rel="noreferrer"
                className="font-medium hover:underline"
              >
                {reviewer.login}
              </a>
              <span className="text-muted-foreground">review requested</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Apps have no user page; their bot account lives under /apps. */
function profileUrl(author: ConversationAuthor | null): string {
  if (!author) return "https://github.com"
  return author.bot
    ? `https://github.com/apps/${author.login.replace(/\[bot\]$/, "")}`
    : `https://github.com/${author.login}`
}

const reviewVerdictWords: Record<ConversationReview["state"], string> = {
  APPROVED: "approved",
  CHANGES_REQUESTED: "requested changes",
  COMMENTED: "commented",
  DISMISSED: "was dismissed",
}

function ReviewVerdictMark({ state }: { state: ConversationReview["state"] }) {
  if (state === "APPROVED")
    return <CheckCircleIcon weight="fill" className="size-3.5 text-success" />
  if (state === "CHANGES_REQUESTED")
    return <XCircleIcon weight="fill" className="size-3.5 text-destructive" />
  return <ChatCircleTextIcon className="size-3.5 text-muted-foreground" />
}

function BranchRow({
  detail,
  status,
  login,
}: {
  detail: ReviewDetail
  status: OpenPullRequest
  login: string
}) {
  const queryClient = useQueryClient()
  if (isConflicted(status))
    return (
      <Row
        icon={failIcon}
        label="Branch"
        action={
          <PullRequestThreadAction
            pr={status}
            login={login}
            action="fix-conflicts"
          />
        }
      >
        Conflicts with {detail.pr.base_ref} must be resolved
      </Row>
    )
  if (status.draft)
    return (
      <Row
        icon={warnIcon}
        label="Branch"
        action={
          <MarkPullRequestReady
            pr={status}
            onReady={() =>
              void queryClient.invalidateQueries({
                queryKey: ["review-page-pr"],
              })
            }
          />
        }
      >
        A draft, so reviewers aren&apos;t notified yet
      </Row>
    )
  if (status.mergeState === "behind" && canUpdateBranch(status))
    return (
      <Row
        icon={warnIcon}
        label="Branch"
        action={
          <UpdatePullRequestBranch
            pr={status}
            onUpdated={() =>
              void queryClient.invalidateQueries({
                queryKey: ["review-page-pr"],
              })
            }
          />
        }
      >
        Behind {detail.pr.base_ref}
      </Row>
    )
  return null
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
  const queryClient = useQueryClient()
  const [merged, setMerged] = useState(false)
  return (
    <Row
      icon={<GitMergeIcon weight="bold" className="size-4 text-success" />}
      label="Merge"
      action={
        <MergePullRequest
          pr={status}
          apply={() => {
            setMerged(true)
            return () => setMerged(false)
          }}
          onMerged={() => {
            void queryClient.invalidateQueries({
              queryKey: reviewQueries.detail(pr).queryKey,
            })
            void queryClient.invalidateQueries({ queryKey: ["review-page-pr"] })
          }}
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
  const bugs = detail.findings.filter(
    (f) => f.group === "bug" && f.status === "open"
  ).length
  const flags = detail.findings.filter(
    (f) => f.group !== "bug" && f.status === "open"
  ).length
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
              ? [
                  bugs && `${bugs} bug${bugs === 1 ? "" : "s"}`,
                  flags && `${flags} flag${flags === 1 ? "" : "s"}`,
                ]
                  .filter(Boolean)
                  .join(", ")
              : "no open findings",
          ]
            .filter(Boolean)
            .join(" · ")
  const prOpen = detail.pr.state === "open"
  return (
    <div>
      <Row
        icon={
          running ? (
            <Spinner className="size-4 text-primary" />
          ) : (
            <AgentMark className="size-4" />
          )
        }
        label="Open SWE"
        expandable={canExpand}
        expanded={expanded}
        onToggle={() => setExpanded((value) => !value)}
        action={
          <>
            {detail.thread_id && (
              <Link
                to="/agents/$threadId"
                params={{ threadId: detail.thread_id }}
                className="text-xs text-muted-foreground underline decoration-muted-foreground/40 underline-offset-2 hover:text-foreground"
              >
                {running ? "Watch it work" : "See how it reviewed"}
              </Link>
            )}
            {prOpen && (
              <Button
                size="sm"
                variant="ghost"
                disabled={running}
                onClick={() => reReview.mutate()}
                className="text-muted-foreground"
              >
                <ArrowClockwiseIcon />
                {detail.status === "none" ? "Review" : "Re-review"}
              </Button>
            )}
          </>
        }
      >
        {summary}
      </Row>
      {expanded && canExpand && (
        <div
          // Remounting replays the flash each time something asks for the findings.
          key={findingsKey}
          className={cn(
            "pr-4 pb-3 pl-11",
            findingsKey > 0 && "motion-safe:animate-attention-flash"
          )}
        >
          {detail.findings.length > 0 && (
            <FindingQueue findings={detail.findings} />
          )}
          {assessment && (
            <div className="mt-2 [&>section]:mt-0 [&>section]:border-dashed [&>section]:bg-transparent [&>section]:p-3 [&>section]:text-xs">
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
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const setExpandedFinding = useReviewPage((state) => state.setExpandedFinding)
  const askInChat = useReviewPage((state) => state.askInChat)
  const [openId, setOpenId] = useState<string | null>(null)
  return (
    <ol className="flex flex-col">
      {rankFindings(findings).map((finding) => {
        const settled = finding.status !== "open"
        const anchored = isAnchored(finding)
        const open = openId === finding.id
        return (
          <li
            key={finding.id}
            className={cn(
              "group relative border-l-2 py-1.5 pl-3",
              settled && "opacity-55"
            )}
            style={{ borderLeftColor: findingGroupColor[finding.group] }}
          >
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs">
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
                  "min-w-[16ch] flex-1 text-left leading-5 text-foreground hover:underline",
                  settled && "line-through decoration-muted-foreground/60"
                )}
              >
                <InlineCode text={finding.title} />
              </button>
              <span className="ml-auto flex shrink-0 items-baseline gap-1 text-[11px] text-muted-foreground">
                <button
                  type="button"
                  disabled={!anchored}
                  onClick={() => {
                    setExpandedFinding(finding.id)
                    jumpTo({
                      kind: "line",
                      path: finding.file,
                      line: finding.end_line ?? 1,
                      start: finding.start_line ?? undefined,
                      side: finding.side,
                    })
                  }}
                  className="rounded px-1 py-0.5 font-mono hover:bg-accent hover:text-foreground disabled:hover:bg-transparent disabled:hover:text-muted-foreground"
                  title={finding.file}
                >
                  {findingLocation(finding)}
                </button>
                <button
                  type="button"
                  aria-label={`Ask Open SWE about: ${finding.title}`}
                  className="rounded px-1 py-0.5 hover:bg-accent hover:text-foreground"
                  onClick={() => askInChat(askAboutFinding(finding))}
                >
                  Ask
                </button>
                {!settled && (
                  <button
                    type="button"
                    aria-label={`Ask Open SWE to fix: ${finding.title}`}
                    className="rounded px-1 py-0.5 hover:bg-accent hover:text-foreground"
                    onClick={() => askInChat(fixFinding(finding))}
                  >
                    Fix it
                  </button>
                )}
              </span>
            </div>
            {open && (
              <div className="markdown-body mt-1.5 max-w-prose text-xs text-muted-foreground">
                <Markdown content={finding.description} />
                {finding.resolution_note && (
                  <p className="mt-1 flex items-center gap-1">
                    <ChatCircleTextIcon className="size-3" />
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
