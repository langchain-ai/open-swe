import { Link, Navigate, createFileRoute } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowClockwiseIcon,
  ArrowLeftIcon,
  BugBeetleIcon,
  CaretDownIcon,
  CheckCircleIcon,
  CheckIcon,
  CircleIcon,
  CopyIcon,
  FlagIcon,
  GitPullRequestIcon,
  InfoIcon,
  XCircleIcon,
  XIcon,
} from "@phosphor-icons/react"
import { IoLogoGithub } from "react-icons/io5"

import type {
  ReviewCheckRun,
  ReviewDetail,
  ReviewDiffFile,
  ReviewDiffLine,
  ReviewFinding,
  ReviewUserRef,
} from "@/lib/api"
import { Markdown } from "@/components/agents/ported"
import { useRegisterReviewSidebar } from "@/components/agents/ReviewSidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/agents/reviews/$owner/$repo/$number")({
  component: ReviewDetailPage,
})

type SideTab = "info" | "chat"

const GROUP_STYLES = {
  bug: { label: "Bug", className: "text-destructive", Icon: BugBeetleIcon },
  investigate: {
    label: "Investigate",
    className: "text-amber-500",
    Icon: FlagIcon,
  },
  informational: {
    label: "Informational",
    className: "text-muted-foreground",
    Icon: InfoIcon,
  },
} as const

function findingAnchorLabel(finding: ReviewFinding): string {
  if (finding.start_line === null || finding.end_line === null)
    return finding.file
  if (finding.start_line === finding.end_line)
    return `${finding.file}:${finding.end_line}`
  return `${finding.file}:${finding.start_line}-${finding.end_line}`
}

function isAnchored(finding: ReviewFinding): boolean {
  return Boolean(finding.file) && finding.in_diff && finding.end_line !== null
}

type HighlightEdge = { top: boolean; bottom: boolean } | null

function highlightRange(
  lines: Array<ReviewDiffLine>,
  finding: ReviewFinding
): { start: number; end: number } | null {
  let start = -1
  let end = -1
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index]
    if (line && lineMatchesFinding(line, finding)) {
      if (start === -1) start = index
      end = index
    }
  }
  return start === -1 ? null : { start, end }
}

function lineMatchesFinding(
  line: ReviewDiffLine,
  finding: ReviewFinding
): boolean {
  if (finding.end_line === null) return false
  const start = finding.start_line ?? finding.end_line
  const lineNumber = finding.side === "LEFT" ? line.old_line : line.new_line
  if (lineNumber === undefined) return false
  if (finding.side === "LEFT" && line.kind !== "del") return false
  if (finding.side === "RIGHT" && line.kind === "del") return false
  return lineNumber >= start && lineNumber <= finding.end_line
}

function isFindingAnchorRow(
  line: ReviewDiffLine,
  finding: ReviewFinding
): boolean {
  if (finding.end_line === null) return false
  const lineNumber = finding.side === "LEFT" ? line.old_line : line.new_line
  if (finding.side === "LEFT" && line.kind !== "del") return false
  if (finding.side === "RIGHT" && line.kind === "del") return false
  return lineNumber === finding.end_line
}

function findingClipboardText(finding: ReviewFinding): string {
  const style = GROUP_STYLES[finding.group]
  const lines = [
    `**${style.label}: ${finding.title}**`,
    `${findingAnchorLabel(finding)}`,
    "",
    finding.description,
  ]
  if (finding.suggestion)
    lines.push("", "```suggestion", finding.suggestion, "```")
  return lines.join("\n")
}

function ReviewDetailPage() {
  const { owner, repo, number } = Route.useParams()
  const prNumber = Number(number)
  const session = useSession()
  const detail = useQuery({
    queryKey: ["review", owner, repo, prNumber],
    queryFn: () => api.getReview(owner, repo, prNumber),
    enabled: !!session.data && Number.isFinite(prNumber),
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 5000 : false,
  })
  const diff = useQuery({
    queryKey: ["reviewDiff", owner, repo, prNumber],
    queryFn: () => api.getReviewDiff(owner, repo, prNumber),
    enabled: !!session.data && Number.isFinite(prNumber),
  })

  const queryClient = useQueryClient()
  const headSha = detail.data?.head_sha
  const seenShaRef = useRef(headSha)
  useEffect(() => {
    if (headSha && seenShaRef.current && headSha !== seenShaRef.current) {
      void queryClient.invalidateQueries({
        queryKey: ["reviewDiff", owner, repo, prNumber],
      })
    }
    if (headSha) seenShaRef.current = headSha
  }, [headSha, queryClient, owner, repo, prNumber])

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <Navigate to="/login" />

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background text-foreground">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border px-4 text-xs">
        <Link
          to="/agents/reviews"
          className="inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground"
        >
          <ArrowLeftIcon className="size-3.5" />
          Reviews
        </Link>
        <span className="text-muted-foreground">/</span>
        <span className="inline-flex min-w-0 items-center gap-1.5 truncate">
          <GitPullRequestIcon className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate font-medium">
            {owner}/{repo}#{number}
            {detail.data ? ` ${detail.data.pr.title}` : ""}
          </span>
        </span>
      </header>

      {detail.error ? (
        <div className="p-6 text-xs text-destructive">
          {detail.error.message}
        </div>
      ) : !detail.data ? (
        <div className="space-y-3 p-6">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      ) : (
        <ReviewBody
          key={detail.data.head_sha}
          detail={detail.data}
          diffFiles={diff.data?.files ?? null}
        />
      )}
    </div>
  )
}

function ReviewBody({
  detail,
  diffFiles,
}: {
  detail: ReviewDetail
  diffFiles: Array<ReviewDiffFile> | null
}) {
  const [sideTab, setSideTab] = useState<SideTab>("info")
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const scrollRef = useRef<HTMLElement | null>(null)
  const fileRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const anchorRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const [expandedFiles, setExpandedFiles] = useState<Record<string, boolean>>(
    {}
  )
  const [focused, setFocused] = useState<ReviewFinding | null>(null)
  const [cardPos, setCardPos] = useState<{ top: number; left: number | null }>(
    { top: 96, left: null }
  )

  const viewedStorageKey = `open-swe.review.viewed.${detail.owner}/${detail.repo}/${detail.number}.${detail.head_sha}`
  const [viewed, setViewed] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set()
    try {
      const raw = window.localStorage.getItem(viewedStorageKey)
      return new Set(raw ? (JSON.parse(raw) as Array<string>) : [])
    } catch {
      return new Set()
    }
  })
  const toggleViewed = useCallback(
    (path: string) => {
      setViewed((prev) => {
        const next = new Set(prev)
        if (next.has(path)) next.delete(path)
        else next.add(path)
        window.localStorage.setItem(
          viewedStorageKey,
          JSON.stringify(Array.from(next))
        )
        return next
      })
    },
    [viewedStorageKey]
  )

  const readStorageKey = `open-swe.review.read.${detail.thread_id}`
  const [read, setRead] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set()
    try {
      const raw = window.localStorage.getItem(readStorageKey)
      return new Set(raw ? (JSON.parse(raw) as Array<string>) : [])
    } catch {
      return new Set()
    }
  })
  const persistRead = useCallback(
    (next: Set<string>) => {
      setRead(next)
      window.localStorage.setItem(
        readStorageKey,
        JSON.stringify(Array.from(next))
      )
    },
    [readStorageKey]
  )
  const markRead = useCallback(
    (id: string) => {
      persistRead(new Set(read).add(id))
    },
    [persistRead, read]
  )
  const markAllRead = useCallback(() => {
    persistRead(new Set(detail.findings.map((f) => f.id)))
  }, [detail.findings, persistRead])

  const findingsByFile = useMemo(() => {
    const byFile = new Map<string, Array<ReviewFinding>>()
    for (const finding of detail.findings) {
      if (!isAnchored(finding)) continue
      const list = byFile.get(finding.file) ?? []
      list.push(finding)
      byFile.set(finding.file, list)
    }
    return byFile
  }, [detail.findings])

  const linesLeft = useMemo(() => {
    if (!diffFiles) return null
    return diffFiles
      .filter((file) => !viewed.has(file.path))
      .reduce((acc, file) => acc + file.additions + file.deletions, 0)
  }, [diffFiles, viewed])

  const scrollToFile = useCallback((path: string) => {
    setSelectedFile(path)
    setExpandedFiles((prev) => ({ ...prev, [path]: true }))
    requestAnimationFrame(() => {
      fileRefs.current[path]?.scrollIntoView({
        block: "start",
        behavior: "smooth",
      })
    })
  }, [])

  const updateCardPos = useCallback((finding: ReviewFinding) => {
    const rect = anchorRefs.current[finding.id]?.getBoundingClientRect()
    if (rect) {
      setCardPos({
        top: Math.min(Math.max(rect.top, 64), window.innerHeight - 360),
        left: Math.min(rect.right + 12, window.innerWidth - 412 - 8),
      })
    } else {
      setCardPos({ top: 96, left: null })
    }
  }, [])

  const openFinding = useCallback(
    (finding: ReviewFinding) => {
      markRead(finding.id)
      setFocused(finding)
      if (!isAnchored(finding)) {
        setCardPos({ top: 96, left: null })
        return
      }
      setExpandedFiles((prev) => ({ ...prev, [finding.file]: true }))
      requestAnimationFrame(() => {
        const node =
          anchorRefs.current[finding.id] ?? fileRefs.current[finding.file]
        node?.scrollIntoView({ block: "center" })
        requestAnimationFrame(() => updateCardPos(finding))
      })
    },
    [markRead, updateCardPos]
  )

  useEffect(() => {
    if (!focused || !isAnchored(focused)) return
    const scroller = scrollRef.current
    const onMove = () => updateCardPos(focused)
    scroller?.addEventListener("scroll", onMove, { passive: true })
    window.addEventListener("resize", onMove)
    return () => {
      scroller?.removeEventListener("scroll", onMove)
      window.removeEventListener("resize", onMove)
    }
  }, [focused, updateCardPos])

  const closeFinding = useCallback(() => setFocused(null), [])

  const sidebarData = useMemo(
    () => ({
      title: `PR #${detail.number}`,
      files: diffFiles,
      selected: selectedFile,
      viewed,
      onSelect: scrollToFile,
    }),
    [detail.number, diffFiles, selectedFile, viewed, scrollToFile]
  )
  useRegisterReviewSidebar(sidebarData)

  useEffect(() => {
    if (!focused) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFocused(null)
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Element && target.closest("[data-finding-card]"))
        return
      setFocused(null)
    }
    window.addEventListener("keydown", onKeyDown)
    window.addEventListener("pointerdown", onPointerDown)
    return () => {
      window.removeEventListener("keydown", onKeyDown)
      window.removeEventListener("pointerdown", onPointerDown)
    }
  }, [focused])

  return (
    <div className="relative flex min-h-0 flex-1">
      <main ref={scrollRef} className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl px-6 py-6">
          <PrHeader detail={detail} />
          <div className="mt-4 rounded-lg border border-border bg-card p-4">
            {detail.pr.body ? (
              <Markdown content={detail.pr.body} />
            ) : (
              <p className="text-xs text-muted-foreground">
                This PR has no description.
              </p>
            )}
          </div>

          <div className="mt-6">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-medium">Changes</h2>
              {linesLeft !== null && (
                <span className="text-xs text-muted-foreground">
                  {linesLeft === 0
                    ? "All lines reviewed"
                    : `${linesLeft} lines left`}
                </span>
              )}
            </div>
            {!diffFiles ? (
              <Skeleton className="h-64 w-full" />
            ) : diffFiles.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No diff available.
              </p>
            ) : (
              <div className="space-y-3">
                {diffFiles.map((file) => (
                  <FileDiffCard
                    key={file.path}
                    file={file}
                    findings={findingsByFile.get(file.path) ?? []}
                    focused={focused}
                    viewed={viewed.has(file.path)}
                    onToggleViewed={() => {
                      const collapses =
                        !viewed.has(file.path) &&
                        expandedFiles[file.path] === undefined
                      if (collapses && focused?.file === file.path)
                        setFocused(null)
                      toggleViewed(file.path)
                    }}
                    expanded={
                      expandedFiles[file.path] ?? !viewed.has(file.path)
                    }
                    onToggleExpanded={() => {
                      const next = !(
                        expandedFiles[file.path] ?? !viewed.has(file.path)
                      )
                      if (!next && focused?.file === file.path)
                        setFocused(null)
                      setExpandedFiles((prev) => ({
                        ...prev,
                        [file.path]: next,
                      }))
                    }}
                    onFindingClick={openFinding}
                    sectionRef={(node) => {
                      fileRefs.current[file.path] = node
                    }}
                    anchorRef={(id, node) => {
                      anchorRefs.current[id] = node
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </main>

      <SidePanel
        detail={detail}
        tab={sideTab}
        onTabChange={setSideTab}
        read={read}
        dimmed={focused !== null}
        onMarkAllRead={markAllRead}
        onFindingClick={openFinding}
      />

      {focused && (
        <FindingFloatingCard
          detail={detail}
          finding={focused}
          top={cardPos.top}
          left={cardPos.left}
          onClose={closeFinding}
        />
      )}
    </div>
  )
}

function PrHeader({ detail }: { detail: ReviewDetail }) {
  const { pr } = detail
  const stateStyles: Record<string, string> = {
    open: "border-emerald-600/40 text-emerald-500",
    draft: "border-border text-muted-foreground",
    merged: "border-purple-600/40 text-purple-500",
    closed: "border-red-600/40 text-red-500",
  }
  return (
    <div>
      <span
        className={cn(
          "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] capitalize",
          stateStyles[pr.state] ?? stateStyles.open
        )}
      >
        <GitPullRequestIcon className="size-3" />
        {pr.state}
      </span>
      <h1 className="mt-2 text-lg font-medium">
        <a
          href={detail.url}
          target="_blank"
          rel="noreferrer"
          className="hover:underline"
        >
          {pr.title}
        </a>
      </h1>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {pr.author && (
          <span className="font-medium text-foreground">{pr.author.login}</span>
        )}
        <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[11px]">
          {pr.base_ref}
        </span>
        <span>←</span>
        <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[11px]">
          {pr.head_ref}
        </span>
        <span>
          {pr.changed_files} file{pr.changed_files === 1 ? "" : "s"}
        </span>
        <span className="text-emerald-500">+{pr.additions}</span>
        <span className="text-red-500">-{pr.deletions}</span>
      </div>
    </div>
  )
}

function FileDiffCard({
  file,
  findings,
  focused,
  viewed,
  onToggleViewed,
  expanded,
  onToggleExpanded,
  onFindingClick,
  sectionRef,
  anchorRef,
}: {
  file: ReviewDiffFile
  findings: Array<ReviewFinding>
  focused: ReviewFinding | null
  viewed: boolean
  onToggleViewed: () => void
  expanded: boolean
  onToggleExpanded: () => void
  onFindingClick: (finding: ReviewFinding) => void
  sectionRef: (node: HTMLDivElement | null) => void
  anchorRef: (id: string, node: HTMLDivElement | null) => void
}) {
  const fileFocused =
    focused?.file === file.path && isAnchored(focused) ? focused : null

  return (
    <div
      ref={sectionRef}
      className="scroll-mt-4 overflow-hidden rounded-lg border border-border"
    >
      <div className="flex items-center gap-2 bg-muted/40 px-3 py-2 text-xs">
        <button
          type="button"
          onClick={onToggleExpanded}
          className="inline-flex items-center gap-2 text-left"
        >
          <CaretDownIcon
            className={cn(
              "size-3 transition-transform",
              !expanded && "-rotate-90"
            )}
          />
          <span className="font-mono font-medium">{file.path}</span>
        </button>
        <span className="flex items-center gap-1.5 font-mono text-[11px]">
          <span className="text-emerald-500">+{file.additions}</span>
          <span className="text-red-500">-{file.deletions}</span>
        </span>
        {findings.length > 0 && (
          <span className="inline-flex items-center gap-1 text-[11px] text-amber-500">
            <FlagIcon className="size-3" />
            {findings.length}
          </span>
        )}
        <label className="ml-auto inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
          Mark as viewed
          <button
            type="button"
            role="checkbox"
            aria-checked={viewed}
            onClick={onToggleViewed}
            className={cn(
              "flex size-4 items-center justify-center rounded border border-border",
              viewed && "bg-foreground text-background"
            )}
          >
            {viewed && <CheckIcon className="size-3" />}
          </button>
        </label>
      </div>
      {expanded && (
        <div className="overflow-x-auto bg-card font-mono text-[11px] leading-5">
          {file.hunks.map((hunk, hunkIndex) => {
            const range =
              fileFocused !== null
                ? highlightRange(hunk.lines, fileFocused)
                : null
            const anchorRows = new Map<number, Array<ReviewFinding>>()
            for (const finding of findings) {
              const index = hunk.lines.findIndex((hunkLine) =>
                lineMatchesFinding(hunkLine, finding)
              )
              if (index !== -1) {
                anchorRows.set(index, [
                  ...(anchorRows.get(index) ?? []),
                  finding,
                ])
              }
            }
            return (
              <div key={hunkIndex}>
                <div className="bg-muted/60 px-3 py-1 text-muted-foreground">
                  {hunk.header}
                </div>
                {hunk.lines.map((line, lineIndex) => {
                  const lineFindings = findings.filter((finding) =>
                    isFindingAnchorRow(line, finding)
                  )
                  const anchorFindings = anchorRows.get(lineIndex) ?? []
                  let highlight: HighlightEdge = null
                  if (
                    range &&
                    lineIndex >= range.start &&
                    lineIndex <= range.end
                  ) {
                    highlight = {
                      top: lineIndex === range.start,
                      bottom: lineIndex === range.end,
                    }
                  }
                  return (
                    <DiffLineRow
                      key={lineIndex}
                      line={line}
                      findings={lineFindings}
                      anchorFindings={anchorFindings}
                      highlight={highlight}
                      onFindingClick={onFindingClick}
                      anchorRef={anchorRef}
                    />
                  )
                })}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function DiffLineRow({
  line,
  findings,
  anchorFindings,
  highlight,
  onFindingClick,
  anchorRef,
}: {
  line: ReviewDiffLine
  findings: Array<ReviewFinding>
  anchorFindings: Array<ReviewFinding>
  highlight: HighlightEdge
  onFindingClick: (finding: ReviewFinding) => void
  anchorRef: (id: string, node: HTMLDivElement | null) => void
}) {
  const first = findings[0]
  return (
    <div
      ref={
        anchorFindings.length > 0
          ? (node) => {
              for (const finding of anchorFindings) anchorRef(finding.id, node)
            }
          : undefined
      }
      className={cn(
        "flex",
        line.kind === "add" && "bg-emerald-500/10",
        line.kind === "del" && "bg-red-500/10",
        highlight && "border-x border-sky-400/50 bg-sky-400/10",
        highlight?.top && "rounded-t-sm border-t",
        highlight?.bottom && "rounded-b-sm border-b"
      )}
    >
      <span className="w-10 shrink-0 px-1 text-right text-muted-foreground/60 select-none">
        {line.old_line ?? ""}
      </span>
      <span className="w-10 shrink-0 px-1 text-right text-muted-foreground/60 select-none">
        {line.new_line ?? ""}
      </span>
      <span
        className={cn(
          "w-4 shrink-0 text-center select-none",
          line.kind === "add" && "text-emerald-500",
          line.kind === "del" && "text-red-500"
        )}
      >
        {line.kind === "add" ? "+" : line.kind === "del" ? "-" : ""}
      </span>
      <span className="pr-3 whitespace-pre">{line.text}</span>
      {first && (
        <button
          type="button"
          onClick={() => onFindingClick(first)}
          aria-label={`Open finding: ${first.title}`}
          className="mr-2 ml-auto shrink-0 self-center"
        >
          {(() => {
            const style = GROUP_STYLES[first.group]
            const Icon = style.Icon
            return <Icon className={cn("size-3.5", style.className)} />
          })()}
        </button>
      )}
    </div>
  )
}

function FindingFloatingCard({
  detail,
  finding,
  top,
  left,
  onClose,
}: {
  detail: ReviewDetail
  finding: ReviewFinding
  top: number
  left: number | null
  onClose: () => void
}) {
  const [copied, setCopied] = useState(false)
  const style = GROUP_STYLES[finding.group]
  const Icon = style.Icon
  const githubUrl =
    finding.github_review_comment_id !== null
      ? `${detail.url}#discussion_r${finding.github_review_comment_id}`
      : null
  const rangeLabel =
    finding.end_line !== null
      ? `${finding.side === "LEFT" ? "L" : "R"}${finding.start_line ?? finding.end_line}${
          finding.start_line !== null && finding.start_line !== finding.end_line
            ? `-${finding.end_line}`
            : ""
        }`
      : finding.file

  const copy = () => {
    void navigator.clipboard
      .writeText(findingClipboardText(finding))
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1500)
      })
  }

  return (
    <div
      data-finding-card
      className={cn(
        "fixed z-50 flex max-h-[70vh] w-[412px] flex-col overflow-hidden rounded-lg border border-border bg-background shadow-2xl",
        left === null && "right-1"
      )}
      style={left === null ? { top } : { top, left }}
      role="dialog"
      aria-label={finding.title}
    >
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5 text-xs">
        <Icon className={cn("size-3.5", style.className)} />
        <span className={cn("font-medium", style.className)}>
          {style.label}
        </span>
        <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {rangeLabel}
        </span>
        {finding.outdated && <Badgeish>Outdated</Badgeish>}
        {finding.status !== "open" && <Badgeish>{finding.status}</Badgeish>}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close finding"
          className="ml-auto rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <XIcon className="size-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <div className="text-sm font-medium">{finding.title}</div>
        <div className="mt-1.5 text-xs text-muted-foreground">
          <Markdown content={finding.description} />
        </div>
        {finding.resolution_note && (
          <p className="mt-2 text-[11px] text-muted-foreground">
            Resolution: {finding.resolution_note}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2 border-t border-border px-4 py-2.5">
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
        >
          <CopyIcon className="size-3" />
          {copied ? "Copied" : "Copy"}
        </button>
        {githubUrl && (
          <a
            href={githubUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <IoLogoGithub className="size-3" />
            View on GitHub
          </a>
        )}
      </div>
    </div>
  )
}

function Badgeish({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground capitalize">
      {children}
    </span>
  )
}

function SidePanel({
  detail,
  tab,
  onTabChange,
  read,
  dimmed,
  onMarkAllRead,
  onFindingClick,
}: {
  detail: ReviewDetail
  tab: SideTab
  onTabChange: (tab: SideTab) => void
  read: Set<string>
  dimmed: boolean
  onMarkAllRead: () => void
  onFindingClick: (finding: ReviewFinding) => void
}) {
  const qc = useQueryClient()
  const reReview = useMutation({
    mutationFn: () => api.reReview(detail.owner, detail.repo, detail.number),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["review", detail.owner, detail.repo, detail.number],
      })
    },
  })

  const bugs = detail.findings.filter((f) => f.group === "bug")
  const flags = detail.findings.filter((f) => f.group !== "bug")
  const openBugs = bugs.filter((f) => f.status === "open")
  const openFlags = flags.filter((f) => f.status === "open")

  return (
    <aside
      className={cn(
        "hidden w-[420px] shrink-0 flex-col overflow-y-auto border-l border-border transition-opacity xl:flex",
        dimmed && "pointer-events-none opacity-30"
      )}
    >
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
        <div className="flex flex-1 items-center justify-center p-6 text-xs text-muted-foreground">
          Coming Soon
        </div>
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
                <div className="text-destructive">{reReview.error.message}</div>
              )}
            </div>
          </section>

          <FindingSection
            icon={BugBeetleIcon}
            label={`${openBugs.length} Bug${openBugs.length === 1 ? "" : "s"}`}
            emptyLabel="No bugs found."
            findings={bugs}
            read={read}
            onFindingClick={onFindingClick}
          />

          <FindingSection
            icon={FlagIcon}
            label={`${openFlags.length} Flag${openFlags.length === 1 ? "" : "s"}`}
            emptyLabel="No issues found."
            findings={flags}
            read={read}
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
  )
}

function FindingSection({
  icon: HeaderIcon,
  label,
  emptyLabel,
  findings,
  read,
  onFindingClick,
  action,
}: {
  icon: (typeof GROUP_STYLES)["bug"]["Icon"]
  label: string
  emptyLabel: string
  findings: Array<ReviewFinding>
  read: Set<string>
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
              return (
                <button
                  key={finding.id}
                  type="button"
                  onClick={() => onFindingClick(finding)}
                  className={cn(
                    "block w-full rounded-md border border-transparent px-2 py-1.5 text-left transition-colors hover:border-border hover:bg-muted/40",
                    muted && "opacity-50"
                  )}
                >
                  <span className="flex items-start gap-1.5 text-xs">
                    <Icon
                      className={cn(
                        "mt-0.5 size-3.5 shrink-0",
                        style.className
                      )}
                    />
                    <span className="min-w-0">
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
                  </span>
                </button>
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
