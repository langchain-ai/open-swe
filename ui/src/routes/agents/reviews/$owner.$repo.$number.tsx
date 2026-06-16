import { Link, Navigate, createFileRoute } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
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
import { MultiFileDiff } from "@pierre/diffs/react"
import type { DiffLineAnnotation, SelectedLineRange } from "@pierre/diffs"

import type {
  ReviewCheckRun,
  ReviewDetail,
  ReviewDiffFile,
  ReviewFinding,
  ReviewUserRef,
} from "@/lib/api"
import type {
  ReviewSidebarGroup,
  ReviewSidebarView,
} from "@/components/agents/ReviewSidebar"
import { Markdown } from "@/components/agents/ported"
import { ReviewChat } from "@/components/agents/ReviewChat"
import { useRegisterReviewSidebar } from "@/components/agents/ReviewSidebar"
import {
  useDiffOptions,
  warmDiffHighlighter,
} from "@/components/agents/utils/diffUtils"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/agents/reviews/$owner/$repo/$number")({
  component: ReviewDetailPage,
})

type SideTab = "info" | "chat"

const REVIEW_VIEW_STORAGE_KEY = "open-swe.review.view"

interface ResolvedGroup {
  index: number
  title: string
  summary: string
  files: Array<ReviewDiffFile>
  additions: number
  deletions: number
}

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

function findingSide(finding: ReviewFinding): "deletions" | "additions" {
  return finding.side === "LEFT" ? "deletions" : "additions"
}

function findingSelectedRange(
  finding: ReviewFinding
): SelectedLineRange | null {
  if (finding.end_line === null) return null
  const side = findingSide(finding)
  return {
    start: finding.start_line ?? finding.end_line,
    end: finding.end_line,
    side,
    endSide: side,
  }
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
            {owner}/{repo}
            <span className="ml-1.5 font-normal text-muted-foreground">
              #{number}
            </span>
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
  const fileRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const anchorRefs = useRef<Record<string, HTMLElement | null>>({})
  const [expandedFiles, setExpandedFiles] = useState<Record<string, boolean>>(
    {}
  )
  const [focused, setFocused] = useState<ReviewFinding | null>(null)
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const groupRefs = useRef<Record<number, HTMLDivElement | null>>({})
  const [selectedRange, setSelectedRange] = useState<{
    file: string
    start: number
    end: number
  } | null>(null)

  useEffect(() => {
    void warmDiffHighlighter()
  }, [])

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

  // Resolve the AI-sorted groups against the actual diff: drop stale groups
  // (generated for a previous head) so the file-tree fallback is used, drop
  // paths no longer in the diff and empty groups, and collect any unassigned
  // files into a trailing "Other changes" group so nothing ever disappears.
  const groupedView = useMemo<Array<ResolvedGroup> | null>(() => {
    if (
      !diffFiles ||
      detail.diff_groups_stale ||
      detail.diff_groups.length === 0
    )
      return null
    const byPath = new Map(diffFiles.map((file) => [file.path, file]))
    const assigned = new Set<string>()
    const resolved: Array<Omit<ResolvedGroup, "index">> = []
    for (const group of detail.diff_groups) {
      const files: Array<ReviewDiffFile> = []
      for (const path of group.files) {
        const file = byPath.get(path)
        if (file && !assigned.has(path)) {
          assigned.add(path)
          files.push(file)
        }
      }
      if (files.length === 0) continue
      resolved.push({
        title: group.title,
        summary: group.summary,
        files,
        additions: files.reduce((acc, file) => acc + file.additions, 0),
        deletions: files.reduce((acc, file) => acc + file.deletions, 0),
      })
    }
    const leftover = diffFiles.filter((file) => !assigned.has(file.path))
    if (leftover.length > 0) {
      resolved.push({
        title: "Other changes",
        summary: "",
        files: leftover,
        additions: leftover.reduce((acc, file) => acc + file.additions, 0),
        deletions: leftover.reduce((acc, file) => acc + file.deletions, 0),
      })
    }
    if (resolved.length === 0) return null
    return resolved.map((group, i) => ({ ...group, index: i + 1 }))
  }, [diffFiles, detail.diff_groups, detail.diff_groups_stale])

  const sidebarGroups = useMemo<Array<ReviewSidebarGroup> | null>(() => {
    if (!groupedView) return null
    return groupedView.map((group) => ({
      index: group.index,
      title: group.title,
      summary: group.summary,
      additions: group.additions,
      deletions: group.deletions,
      fileCount: group.files.length,
      files: group.files.map((file) => file.path),
    }))
  }, [groupedView])

  // The view follows fresh-group availability until the user explicitly picks
  // one, after which the choice persists across PRs.
  const hasFreshGroups =
    detail.diff_groups.length > 0 && !detail.diff_groups_stale
  const [explicitView, setExplicitView] = useState<ReviewSidebarView | null>(
    () => {
      if (typeof window === "undefined") return null
      const stored = window.localStorage.getItem(REVIEW_VIEW_STORAGE_KEY)
      return stored === "ai" || stored === "files" ? stored : null
    }
  )
  const view: ReviewSidebarView =
    explicitView ?? (hasFreshGroups ? "ai" : "files")
  const setView = useCallback((next: ReviewSidebarView) => {
    setExplicitView(next)
    if (typeof window !== "undefined") {
      window.localStorage.setItem(REVIEW_VIEW_STORAGE_KEY, next)
    }
  }, [])

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

  const scrollToGroup = useCallback((index: number) => {
    requestAnimationFrame(() => {
      groupRefs.current[index]?.scrollIntoView({
        block: "start",
        behavior: "smooth",
      })
    })
  }, [])

  const scrollToLineRange = useCallback(
    (file: string, start: number, end: number) => {
      setSelectedFile(file)
      setSelectedRange({ file, start, end })
      setExpandedFiles((prev) => ({ ...prev, [file]: true }))
      requestAnimationFrame(() => {
        fileRefs.current[file]?.scrollIntoView({
          block: "start",
          behavior: "smooth",
        })
      })
    },
    []
  )

  const openFinding = useCallback(
    (finding: ReviewFinding) => {
      markRead(finding.id)
      setFocused(finding)
      if (!isAnchored(finding)) {
        setAnchorEl(null)
        return
      }
      setExpandedFiles((prev) => ({ ...prev, [finding.file]: true }))
      requestAnimationFrame(() => {
        const node =
          anchorRefs.current[finding.id] ?? fileRefs.current[finding.file]
        node?.scrollIntoView({ block: "center" })
        setAnchorEl(anchorRefs.current[finding.id] ?? null)
      })
    },
    [markRead]
  )

  const closeFinding = useCallback(() => setFocused(null), [])

  const renderFileCard = (file: ReviewDiffFile) => (
    <FileDiffCard
      key={file.path}
      file={file}
      findings={findingsByFile.get(file.path) ?? []}
      focused={focused}
      selectedRange={selectedRange}
      viewed={viewed.has(file.path)}
      onToggleViewed={() => {
        const becomingViewed = !viewed.has(file.path)
        if (becomingViewed && focused?.file === file.path) setFocused(null)
        toggleViewed(file.path)
        setExpandedFiles((prev) => ({
          ...prev,
          [file.path]: !becomingViewed,
        }))
      }}
      expanded={expandedFiles[file.path] ?? !viewed.has(file.path)}
      onToggleExpanded={() => {
        const next = !(expandedFiles[file.path] ?? !viewed.has(file.path))
        if (!next && focused?.file === file.path) setFocused(null)
        setExpandedFiles((prev) => ({ ...prev, [file.path]: next }))
      }}
      onFindingClick={openFinding}
      sectionRef={(node) => {
        fileRefs.current[file.path] = node
      }}
      anchorRef={(id, node) => {
        anchorRefs.current[id] = node
      }}
    />
  )

  const sidebarData = useMemo(
    () => ({
      title: `PR #${detail.number}`,
      files: diffFiles,
      selected: selectedFile,
      viewed,
      onSelect: scrollToFile,
      groups: sidebarGroups,
      view,
      onViewChange: setView,
      onSelectGroup: scrollToGroup,
      onLocationClick: scrollToLineRange,
    }),
    [
      detail.number,
      diffFiles,
      selectedFile,
      viewed,
      scrollToFile,
      sidebarGroups,
      view,
      setView,
      scrollToGroup,
      scrollToLineRange,
    ]
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
    <div
      ref={scrollRef}
      className="relative flex min-h-0 flex-1 overflow-y-auto"
    >
      <main className="min-w-0 flex-1">
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
            ) : view === "ai" && groupedView ? (
              <div className="space-y-6">
                {groupedView.map((group) => (
                  <div
                    key={group.index}
                    ref={(node) => {
                      groupRefs.current[group.index] = node
                    }}
                    className="scroll-mt-4 space-y-3"
                  >
                    <GroupHeader group={group} />
                    {group.files.map(renderFileCard)}
                  </div>
                ))}
              </div>
            ) : (
              <div className="space-y-3">{diffFiles.map(renderFileCard)}</div>
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

      {focused &&
        (isAnchored(focused) && anchorEl ? (
          <AnchoredFindingCard
            key={focused.id}
            detail={detail}
            finding={focused}
            anchorEl={anchorEl}
            scrollRef={scrollRef}
            onClose={closeFinding}
          />
        ) : (
          <div
            data-finding-card
            role="dialog"
            aria-label={focused.title}
            className={cn(FINDING_CARD_CLASS, "fixed top-24 right-1 z-50")}
          >
            <FindingCardContent
              detail={detail}
              finding={focused}
              onClose={closeFinding}
            />
          </div>
        ))}
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
      <h1 className="mt-2 text-base font-medium">
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

function GroupHeader({ group }: { group: ResolvedGroup }) {
  return (
    <div className="flex items-center gap-2">
      <span className="flex size-5 shrink-0 items-center justify-center rounded bg-[var(--ui-panel-2)] text-[11px] font-medium text-muted-foreground">
        {group.index}
      </span>
      <h3 className="min-w-0 truncate text-sm font-medium">{group.title}</h3>
      <span className="flex shrink-0 items-center gap-1.5 font-mono text-[11px]">
        {group.additions > 0 && (
          <span className="text-emerald-500">+{group.additions}</span>
        )}
        {group.deletions > 0 && (
          <span className="text-red-500">-{group.deletions}</span>
        )}
      </span>
    </div>
  )
}

function FileDiffCard({
  file,
  findings,
  focused,
  selectedRange,
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
  selectedRange: { file: string; start: number; end: number } | null
  viewed: boolean
  onToggleViewed: () => void
  expanded: boolean
  onToggleExpanded: () => void
  onFindingClick: (finding: ReviewFinding) => void
  sectionRef: (node: HTMLDivElement | null) => void
  anchorRef: (id: string, node: HTMLElement | null) => void
}) {
  const diffOptions = useDiffOptions()

  const lineAnnotations = useMemo<Array<DiffLineAnnotation<ReviewFinding>>>(
    () =>
      findings
        .filter((finding) => finding.end_line !== null)
        .map((finding) => ({
          side: findingSide(finding),
          lineNumber: finding.end_line as number,
          metadata: finding,
        })),
    [findings]
  )

  const selectedLines = useMemo<SelectedLineRange | null>(() => {
    if (focused?.file === file.path && isAnchored(focused)) {
      return findingSelectedRange(focused)
    }
    if (selectedRange?.file === file.path) {
      return {
        start: selectedRange.start,
        end: selectedRange.end,
        side: "additions",
        endSide: "additions",
      }
    }
    return null
  }, [focused, selectedRange, file.path])

  return (
    <div
      ref={sectionRef}
      className="scroll-mt-4 overflow-hidden rounded-lg border border-[var(--ui-border)]"
    >
      <div className="flex items-center gap-2 bg-[var(--ui-panel-2)] px-3 py-2 text-xs">
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
      {expanded &&
        (file.unrenderable ? (
          <div className="bg-[var(--ui-panel)] p-4 text-center text-xs text-[var(--ui-text-dim)]">
            Binary or large file — diff not shown.
          </div>
        ) : (
          <div className="overflow-x-auto bg-[var(--ui-panel)] font-mono text-[11px] leading-5">
            <MultiFileDiff<ReviewFinding>
              oldFile={{ name: file.path, contents: file.originalContent }}
              newFile={{ name: file.path, contents: file.modifiedContent }}
              options={diffOptions}
              lineAnnotations={lineAnnotations}
              selectedLines={selectedLines}
              renderAnnotation={(annotation) => (
                <FindingRailMarker
                  finding={annotation.metadata}
                  onFindingClick={onFindingClick}
                  anchorRef={anchorRef}
                />
              )}
            />
          </div>
        ))}
    </div>
  )
}

function FindingRailMarker({
  finding,
  onFindingClick,
  anchorRef,
}: {
  finding: ReviewFinding
  onFindingClick: (finding: ReviewFinding) => void
  anchorRef: (id: string, node: HTMLElement | null) => void
}) {
  const style = GROUP_STYLES[finding.group]
  const Icon = style.Icon
  return (
    <div className="flex justify-end px-2 py-0.5">
      <button
        ref={(node) => anchorRef(finding.id, node)}
        type="button"
        onClick={() => onFindingClick(finding)}
        aria-label={`Open finding: ${finding.title}`}
        className={cn(
          "inline-flex items-center gap-1 rounded border border-[var(--ui-border)] bg-[var(--ui-surface)] px-1.5 py-0.5 text-[10px]",
          style.className
        )}
      >
        <span className="font-sans">{finding.title}</span>
        <Icon className="size-3 shrink-0" />
      </button>
    </div>
  )
}

const FINDING_CARD_WIDTH = 412
const FINDING_CARD_GAP = 12

const FINDING_CARD_CLASS =
  "flex max-h-[70vh] w-[412px] flex-col overflow-hidden rounded-lg border border-border bg-background shadow-2xl"

// Positioned absolutely inside the scroll container so it scrolls natively
// with the diff — no per-scroll JS repositioning, hence no lag. Position is
// recomputed only when layout shifts (files expand/collapse, resize).
function AnchoredFindingCard({
  detail,
  finding,
  anchorEl,
  scrollRef,
  onClose,
}: {
  detail: ReviewDetail
  finding: ReviewFinding
  anchorEl: HTMLElement
  scrollRef: React.RefObject<HTMLDivElement | null>
  onClose: () => void
}) {
  const cardRef = useRef<HTMLDivElement | null>(null)

  useLayoutEffect(() => {
    const card = cardRef.current
    const scroller = scrollRef.current
    if (!card || !scroller) return

    const position = () => {
      if (!anchorEl.isConnected) return
      const scrollerRect = scroller.getBoundingClientRect()
      const anchorRect = anchorEl.getBoundingClientRect()
      const top = anchorRect.top - scrollerRect.top + scroller.scrollTop
      const left = Math.max(
        FINDING_CARD_GAP,
        Math.min(
          anchorRect.right -
            scrollerRect.left +
            scroller.scrollLeft +
            FINDING_CARD_GAP,
          scroller.clientWidth - FINDING_CARD_WIDTH - FINDING_CARD_GAP
        )
      )
      card.style.top = `${top}px`
      card.style.left = `${left}px`
    }

    position()
    const observer = new ResizeObserver(position)
    observer.observe(scroller)
    for (const child of scroller.children) {
      if (child !== card) observer.observe(child)
    }
    return () => observer.disconnect()
  }, [anchorEl, scrollRef])

  return (
    <div
      ref={cardRef}
      data-finding-card
      role="dialog"
      aria-label={finding.title}
      className={cn(FINDING_CARD_CLASS, "absolute z-50")}
    >
      <FindingCardContent detail={detail} finding={finding} onClose={onClose} />
    </div>
  )
}

function FindingCardContent({
  detail,
  finding,
  onClose,
}: {
  detail: ReviewDetail
  finding: ReviewFinding
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
    <>
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
    </>
  )
}

function Badgeish({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground capitalize">
      {children}
    </span>
  )
}

const REVIEW_PANEL_STORAGE_WIDTH = "open-swe.review-panel.width"
const REVIEW_PANEL_DEFAULT_WIDTH = 420
const REVIEW_PANEL_MIN_WIDTH = 360
// Keep at least this much room for the PR content column so the panel can grow
// wide without squeezing the diff/description below a usable width.
const REVIEW_PANEL_MIN_MAIN_WIDTH = 480

function reviewPanelMaxWidth(availableWidth?: number): number {
  if (typeof window === "undefined") return REVIEW_PANEL_DEFAULT_WIDTH
  const available = availableWidth ?? window.innerWidth
  return Math.max(
    REVIEW_PANEL_MIN_WIDTH,
    available - REVIEW_PANEL_MIN_MAIN_WIDTH
  )
}

function clampReviewPanelWidth(width: number, availableWidth?: number): number {
  return Math.min(
    reviewPanelMaxWidth(availableWidth),
    Math.max(REVIEW_PANEL_MIN_WIDTH, width)
  )
}

function readStoredReviewPanelWidth(): number {
  if (typeof window === "undefined") return REVIEW_PANEL_DEFAULT_WIDTH
  const raw = window.localStorage.getItem(REVIEW_PANEL_STORAGE_WIDTH)
  const parsed = raw ? Number(raw) : NaN
  if (!Number.isFinite(parsed)) return REVIEW_PANEL_DEFAULT_WIDTH
  return clampReviewPanelWidth(parsed)
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

  const panelRef = useRef<HTMLDivElement>(null)
  const [width, setWidthState] = useState(() => readStoredReviewPanelWidth())
  const setWidth = useCallback((next: number) => {
    const available = panelRef.current?.parentElement?.clientWidth
    const clamped = clampReviewPanelWidth(next, available)
    setWidthState(clamped)
    if (typeof window !== "undefined") {
      window.localStorage.setItem(REVIEW_PANEL_STORAGE_WIDTH, String(clamped))
    }
  }, [])

  // Re-clamp against the real container width on mount and on window resize so
  // the panel can never squeeze the PR content below its minimum.
  useEffect(() => {
    if (typeof window === "undefined") return
    const reclamp = () => setWidth(width)
    reclamp()
    window.addEventListener("resize", reclamp)
    return () => window.removeEventListener("resize", reclamp)
  }, [setWidth, width])

  const bugs = detail.findings.filter((f) => f.group === "bug")
  const flags = detail.findings.filter((f) => f.group !== "bug")
  const openBugs = bugs.filter((f) => f.status === "open")
  const openFlags = flags.filter((f) => f.status === "open")

  return (
    <div
      ref={panelRef}
      style={{ width }}
      className="sticky top-0 hidden h-full shrink-0 xl:flex"
    >
      <ReviewPanelResizeHandle width={width} onResize={setWidth} />
      <aside
        className={cn(
          "flex h-full w-full flex-col overflow-y-auto border-l border-border transition-opacity",
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
    </div>
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
