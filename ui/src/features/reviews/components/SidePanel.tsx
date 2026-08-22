import { useEffect, useRef, useState } from "react"
import {
  ArrowClockwiseIcon,
  BugBeetleIcon,
  CaretDownIcon,
  CheckCircleIcon,
  CircleIcon,
  FlagIcon,
  XCircleIcon,
} from "@phosphor-icons/react"

import type { Icon } from "@phosphor-icons/react"
import type {
  ReviewCheckRun,
  ReviewDetail,
  ReviewFinding,
  ReviewUserRef,
} from "@/features/reviews/lib/api"
import { ReviewChat } from "@/features/reviews/components/ReviewChat"
import {
  Badgeish,
  FindingDetails,
} from "@/features/reviews/components/InlineFinding"
import {
  GROUP_STYLES,
  findingAnchorLabel,
  isAnchored,
} from "@/features/reviews/lib/findings"
import { useReReview } from "@/features/reviews/lib/queries"
import { useReviewPanelWidth } from "@/features/reviews/lib/reviewPrefs"
import { cn } from "@/lib/utils"

export type SideTab = "info" | "chat"

export function SidePanel({
  detail,
  tab,
  onTabChange,
  read,
  expandedId,
  onMarkAllRead,
  onFindingClick,
}: {
  detail: ReviewDetail
  tab: SideTab
  onTabChange: (tab: SideTab) => void
  read: Set<string>
  expandedId: string | null
  onMarkAllRead: () => void
  onFindingClick: (finding: ReviewFinding) => void
}) {
  const reReview = useReReview(detail.owner, detail.repo, detail.number)
  const panelRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useReviewPanelWidth(panelRef)

  const bugs = detail.findings.filter((f) => f.group === "bug")
  const flags = detail.findings.filter((f) => f.group !== "bug")
  const openBugs = bugs.filter((f) => f.status === "open")
  const openFlags = flags.filter((f) => f.status === "open")

  return (
    <div
      ref={panelRef}
      style={{ width }}
      className="relative hidden h-full shrink-0 xl:flex"
    >
      <ReviewPanelResizeHandle width={width} onResize={setWidth} />
      <aside className="flex h-full w-full flex-col overflow-y-auto border-l border-border">
        <div className="flex items-center gap-1 border-b border-border px-3 py-2">
          {(
            [
              ["info", "Info"],
              ["chat", "Chat"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => onTabChange(id)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs transition-colors",
                tab === id
                  ? "bg-muted font-medium text-foreground"
                  : "text-muted-foreground hover:bg-muted/50"
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === "chat" ? (
          <ReviewChat
            owner={detail.owner}
            repo={detail.repo}
            number={detail.number}
          />
        ) : (
          <div className="divide-y divide-border">
            <section className="px-3 py-3">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium">
                  {detail.status === "running"
                    ? "PR analysis in progress"
                    : detail.status === "error"
                      ? "PR analysis failed"
                      : "PR analysis complete"}
                </span>
                <button
                  type="button"
                  onClick={() => reReview.mutate()}
                  disabled={reReview.isPending || detail.status === "running"}
                  className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-50"
                >
                  <ArrowClockwiseIcon className="size-3" />
                  Re-review
                </button>
              </div>
              <div className="mt-2 space-y-1 text-[11px] text-muted-foreground">
                <div>Reviewing commit {detail.head_sha.slice(0, 7) || "—"}</div>
                {detail.watch && <div>Watching for new pushes</div>}
                {reReview.error && (
                  <div className="text-destructive">
                    {reReview.error.message}
                  </div>
                )}
              </div>
            </section>

            <FindingSection
              icon={BugBeetleIcon}
              label={`${openBugs.length} Bug${openBugs.length === 1 ? "" : "s"}`}
              emptyLabel="No bugs found."
              findings={bugs}
              read={read}
              expandedId={expandedId}
              reviewUrl={detail.url}
              onFindingClick={onFindingClick}
            />

            <FindingSection
              icon={FlagIcon}
              label={`${openFlags.length} Flag${openFlags.length === 1 ? "" : "s"}`}
              emptyLabel="No issues found."
              findings={flags}
              read={read}
              expandedId={expandedId}
              reviewUrl={detail.url}
              onFindingClick={onFindingClick}
              action={
                detail.findings.length > 0 ? (
                  <button
                    type="button"
                    onClick={onMarkAllRead}
                    className="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
                  >
                    Mark all as read
                  </button>
                ) : null
              }
            />

            <ChecksSection checks={detail.checks} />
            <PeopleSection
              title="Reviewers"
              people={detail.pr.requested_reviewers}
            />
            <PeopleSection title="Assignees" people={detail.pr.assignees} />
            <section className="px-3 py-3">
              <h3 className="mb-2 text-xs font-medium">Labels</h3>
              {detail.pr.labels.length === 0 ? (
                <p className="text-[11px] text-muted-foreground">None</p>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {detail.pr.labels.map((label) => (
                    <span
                      key={label.name}
                      className="rounded-full border border-border px-2 py-0.5 text-[11px]"
                    >
                      {label.name}
                    </span>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </aside>
    </div>
  )
}

function ReviewPanelResizeHandle({
  width,
  onResize,
}: {
  width: number
  onResize: (next: number) => void
}) {
  const startRef = useRef<{ x: number; width: number } | null>(null)
  const [dragging, setDragging] = useState(false)

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    startRef.current = { x: e.clientX, width }
    setDragging(true)
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!startRef.current) return
    onResize(startRef.current.width - (e.clientX - startRef.current.x))
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    startRef.current = null
    setDragging(false)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }

  useEffect(() => {
    if (!dragging) return
    const prev = document.body.style.cursor
    document.body.style.cursor = "col-resize"
    return () => {
      document.body.style.cursor = prev
    }
  }, [dragging])

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      className={cn(
        "absolute inset-y-0 left-0 z-20 w-1 cursor-col-resize touch-none select-none",
        "after:absolute after:inset-y-0 after:left-0 after:w-px after:bg-transparent after:transition-colors",
        "hover:after:bg-border",
        dragging && "after:bg-border"
      )}
    />
  )
}

function FindingSection({
  icon: HeaderIcon,
  label,
  emptyLabel,
  findings,
  read,
  expandedId,
  reviewUrl,
  onFindingClick,
  action,
}: {
  icon: Icon
  label: string
  emptyLabel: string
  findings: Array<ReviewFinding>
  read: Set<string>
  expandedId: string | null
  reviewUrl: string
  onFindingClick: (finding: ReviewFinding) => void
  action?: React.ReactNode
}) {
  const [collapsed, setCollapsed] = useState(false)
  return (
    <section className="px-3 py-3">
      <div className="mb-2 flex items-center justify-between text-xs">
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="inline-flex items-center gap-1.5 font-medium"
        >
          <HeaderIcon className="size-3.5" />
          {label}
          <CaretDownIcon
            className={cn(
              "size-3 text-muted-foreground transition-transform",
              collapsed && "-rotate-90"
            )}
          />
        </button>
        {action}
      </div>
      {!collapsed &&
        (findings.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">{emptyLabel}</p>
        ) : (
          <div className="space-y-0.5">
            {findings.map((finding) => {
              const style = GROUP_STYLES[finding.group]
              const Icon = style.Icon
              const isRead = read.has(finding.id)
              const muted = finding.status !== "open" || isRead
              const anchored = isAnchored(finding)
              const expanded = expandedId === finding.id && !anchored
              return (
                <div
                  key={finding.id}
                  className={cn(
                    "rounded-md border border-transparent transition-colors hover:border-border hover:bg-muted/40",
                    expanded && "border-border bg-muted/40",
                    muted && !expanded && "opacity-50"
                  )}
                >
                  <button
                    type="button"
                    onClick={() => onFindingClick(finding)}
                    aria-expanded={anchored ? undefined : expanded}
                    className="block w-full px-2 py-1.5 text-left"
                  >
                    <span className="flex items-start gap-1.5 text-xs">
                      <Icon
                        className={cn(
                          "mt-0.5 size-3.5 shrink-0",
                          style.className
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="line-clamp-1 font-medium text-foreground">
                          {finding.title || finding.description}
                        </span>
                        <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                          <span className={style.className}>{style.label}</span>
                          <span className="truncate font-mono">
                            {findingAnchorLabel(finding)}
                          </span>
                          {finding.outdated && <Badgeish>Outdated</Badgeish>}
                          {finding.status !== "open" && (
                            <Badgeish>{finding.status}</Badgeish>
                          )}
                          {isRead && finding.status === "open" && (
                            <span>• Read</span>
                          )}
                        </span>
                      </span>
                      {!anchored && (
                        <CaretDownIcon
                          className={cn(
                            "mt-0.5 size-3 shrink-0 text-muted-foreground transition-transform",
                            !expanded && "-rotate-90"
                          )}
                        />
                      )}
                    </span>
                  </button>
                  {expanded && (
                    <FindingDetails finding={finding} reviewUrl={reviewUrl} />
                  )}
                </div>
              )
            })}
          </div>
        ))}
    </section>
  )
}

function ChecksSection({ checks }: { checks: Array<ReviewCheckRun> }) {
  return (
    <section className="px-3 py-3">
      <h3 className="mb-2 text-xs font-medium">Checks</h3>
      {checks.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No checks reported.</p>
      ) : (
        <div className="max-h-56 space-y-1 overflow-y-auto">
          {checks.map((check, index) =>
            check.url ? (
              <a
                key={`${check.name}-${index}`}
                href={check.url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground"
              >
                <CheckStatusIcon check={check} />
                <span className="truncate">{check.name}</span>
              </a>
            ) : (
              <span
                key={`${check.name}-${index}`}
                className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
              >
                <CheckStatusIcon check={check} />
                <span className="truncate">{check.name}</span>
              </span>
            )
          )}
        </div>
      )}
    </section>
  )
}

function CheckStatusIcon({ check }: { check: ReviewCheckRun }) {
  if (check.status !== "completed") {
    return (
      <CircleIcon className="size-3.5 shrink-0 animate-pulse text-amber-500" />
    )
  }
  if (check.conclusion === "success" || check.conclusion === "neutral") {
    return <CheckCircleIcon className="size-3.5 shrink-0 text-emerald-500" />
  }
  if (check.conclusion === "skipped") {
    return <CircleIcon className="size-3.5 shrink-0 text-muted-foreground" />
  }
  return <XCircleIcon className="size-3.5 shrink-0 text-red-500" />
}

function PeopleSection({
  title,
  people,
}: {
  title: string
  people: Array<ReviewUserRef>
}) {
  return (
    <section className="px-3 py-3">
      <h3 className="mb-2 text-xs font-medium">{title}</h3>
      {people.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">None</p>
      ) : (
        <div className="space-y-1">
          {people.map((person) => (
            <div
              key={person.login}
              className="flex items-center gap-2 text-[11px]"
            >
              {person.avatar_url ? (
                <img
                  src={person.avatar_url}
                  alt=""
                  className="size-4 rounded-full"
                />
              ) : (
                <span className="size-4 rounded-full bg-muted" />
              )}
              {person.login}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
