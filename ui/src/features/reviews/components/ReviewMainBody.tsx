import {
  type CommittedDiffSelection,
  readDiffSelection,
  useDiffLineSelection,
} from "@/features/agents/utils/diffSelection"
import { DiffSelectionPopover } from "@/features/agents/components/DiffSelectionPopover"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  Fragment,
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { Link } from "@tanstack/react-router"
import { toast } from "sonner"
import {
  FileDiff,
  PatchDiff,
  Virtualizer,
  WorkerPoolContextProvider,
  useVirtualizer,
} from "@pierre/diffs/react"
import type {
  FileDiff as CoreFileDiff,
  DiffLineAnnotation,
  FileDiffLoadedFiles,
  FileDiffMetadata,
  SelectedLineRange,
  SelectionSide,
} from "@pierre/diffs"

import type {
  PrReviewComment,
  ReviewCheckRun,
  ReviewCommentsPayload,
  ReviewDetail,
  ReviewDiffFile,
  ReviewFinding,
  PendingReviewComment,
  ReviewUserRef,
  ScoutProgress,
} from "@/lib/api"
import type {
  ReviewSidebarGroup,
  ReviewSidebarView,
} from "@/features/reviews/components/ReviewSidebar"
import { selectionExcerpts } from "@/features/agents/utils/codeExcerpt"
import type {
  DiffRange,
  ProposedComment,
} from "@/features/reviews/lib/chatDiffActions"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { ProposedCommentCard } from "@/features/reviews/components/ProposedCommentCard"
import { ReviewPageActions } from "@/features/reviews/components/ReviewPageActions"
import { PendingReviewCommentCard } from "@/features/reviews/components/PendingReviewCommentCard"
import { ReviewConversation } from "@/features/reviews/components/ReviewConversation"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import {
  buildCommentPayload,
  commentRangeLabel,
} from "@/features/reviews/lib/lineRange"
import type { DiffStyle } from "@/features/agents/utils/diffUtils"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { DiffWrapToggle } from "@/features/agents/components/DiffWrapToggle"
import { agentThreadKeys } from "@/features/agents/lib/queries"
import { HumanInputCard } from "@/features/reviews/components/HumanInputCard"
import { PrHeader } from "@/features/reviews/components/PrHeader"
import { ReviewAssessmentCard } from "@/features/reviews/components/ReviewAssessmentCard"
import {
  rangeLineCount,
  walkthroughFileDiff,
} from "@/features/reviews/lib/walkthroughDiff"
import {
  ReviewChat,
  ReviewChatComposerProvider,
  useReviewChatComposer,
} from "@/features/reviews/components/ReviewChat"
import {
  ReviewSidebarPanel,
  renderInlineCode,
} from "@/features/reviews/components/ReviewSidebar"
import {
  DIFF_VIRTUALIZER_CONFIG,
  DIFF_VIRTUAL_METRICS,
  DIFF_WORKER_HIGHLIGHTER_OPTIONS,
  DIFF_WORKER_POOL_OPTIONS,
  fileContentsCacheKey,
  useDiffOptions,
  warmDiffHighlighter,
} from "@/features/agents/utils/diffUtils"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { ReviewBand } from "@langchain/gtm-platform-design-system/patterns/review-band"
import { Avatar } from "@langchain/gtm-platform-design-system/ui/avatar"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Checkbox } from "@langchain/gtm-platform-design-system/ui/checkbox"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import {
  Button,
  buttonVariants,
} from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { IconWell } from "@langchain/gtm-platform-design-system/ui/icon-well"
import {
  Sheet,
  SheetContent,
} from "@langchain/gtm-platform-design-system/ui/sheet"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Shortcut } from "@langchain/gtm-platform-design-system/ui/shortcut"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@langchain/gtm-platform-design-system/ui/toggle-group"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@langchain/gtm-platform-design-system/ui/tooltip"
import {
  AlertTriangle,
  Bold,
  Bug,
  Check,
  CheckCircle,
  ChevronDown,
  Circle,
  Code,
  Copy,
  ExternalLink,
  FileText,
  Flag,
  GitHub,
  Heading,
  Info,
  Italic,
  Link as LinkGlyph,
  List,
  ListChecks,
  ListOrdered,
  MessageSquare,
  Pencil,
  Quote,
  RefreshCw,
  Rows,
  Columns,
  X,
  XCircle,
  type Glyph,
} from "@/components/glyphs"
import { api, reviewImageProxyUrl } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { useSession } from "@/lib/session"
import { loadReviewFileContents } from "@/features/reviews/lib/fileContents"
import { useMediaQuery } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

type SideTab = "info" | "chat"
type SidePanelLayout = "inline" | "sheet"
// Tailwind's `xl` breakpoint: below it the side panel opens as a sheet.
const WIDE_MEDIA_QUERY = "(min-width: 1280px)"

type ReviewRef = Pick<ReviewDetail, "owner" | "repo" | "number">

// Metadata carried by a Pierre diff line annotation. Findings render as the
// read-only InlineFinding card; a draftComment renders the inline composer; a
// comment renders an existing PR comment opened from the comments dropdown.
type ReviewAnnotation =
  | { kind: "finding"; finding: ReviewFinding }
  | { kind: "draftComment"; path: string; range: SelectedLineRange }
  | { kind: "comment"; comment: PrReviewComment }
  | { kind: "chatDraft"; draft: ProposedComment }
  | { kind: "pending"; comment: PendingReviewComment }

const REVIEW_VIEW_STORAGE_KEY = "open-swe.review.view"
const REVIEW_DIFF_STYLE_STORAGE_KEY = "open-swe.review.diffStyle"
const FINDING_SCROLL_MAX_FRAMES = 120

function readStoredDiffStyle(): DiffStyle {
  if (typeof window === "undefined") return "unified"
  return window.localStorage.getItem(REVIEW_DIFF_STYLE_STORAGE_KEY) === "split"
    ? "split"
    : "unified"
}

// Scroll a file card / group flush to the top of the diff scroller (fallback
// when no virtualizer geometry is available). Jumps instantly to a bounding-rect
// target — respecting the element's scroll-margin-top — then holds that target
// as content above reflows, so no smooth-scroll animation races the height
// reconciliation. Returns a stop fn to cancel the hold.
function scrollCardToTop(
  el: HTMLElement,
  scroller: HTMLElement | null
): () => void {
  if (!scroller) {
    el.scrollIntoView({ block: "start" })
    return () => {}
  }
  return jumpAndHold(scroller, () => {
    const marginTop = parseFloat(getComputedStyle(el).scrollMarginTop) || 0
    const delta =
      el.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top -
      marginTop
    return clampScrollTop(scroller, scroller.scrollTop + delta)
  })
}

// The virtualizer instance returned by useVirtualizer(); exposes
// getOffsetInScrollContainer for accurate scroll targeting.
type DiffVirtualizer = NonNullable<ReturnType<typeof useVirtualizer>>

// Breathing room left above a block/file when it's scrolled to the top.
const SCROLL_TOP_GAP = 8

// Scroll a block / file card flush to the top of the diff scroller using the
// virtualizer's own geometry. getOffsetInScrollContainer returns the element's
// absolute offset within the scroll content; with uniform fixed-height rows
// (see diffUtils) that offset is stable, so an instant jump lands precisely.
// jumpAndHold then re-reads the offset whenever the content reflows (rows above
// measuring/expanding) and re-asserts it, so the target stays pinned to the top.
// Returns a stop fn to cancel the hold.
function scrollCardToTopVirtual(
  el: HTMLElement,
  scroller: HTMLElement,
  virtualizer: DiffVirtualizer
): () => void {
  return jumpAndHold(scroller, () =>
    clampScrollTop(
      scroller,
      virtualizer.getOffsetInScrollContainer(el) - SCROLL_TOP_GAP
    )
  )
}

// Older stored summaries embed `[label](#loc=path:line)` diff links; render the
// label as inline code instead so no stale jump-links leak into the block body.
function stripLocationLinks(summary: string): string {
  return summary.replace(/\[([^\]]+)\]\(#loc=[^)]*\)/g, "`$1`")
}

interface PositionedDiffInstance {
  getLinePosition: (
    lineNumber: number,
    side?: SelectionSide
  ) => { top: number; height: number } | undefined
}

interface RegisteredDiffInstance {
  host: HTMLElement
  instance: CoreFileDiff<ReviewAnnotation>
}

function hasLinePosition(
  instance: CoreFileDiff<ReviewAnnotation>
): instance is CoreFileDiff<ReviewAnnotation> & PositionedDiffInstance {
  return (
    typeof (instance as { getLinePosition?: unknown }).getLinePosition ===
    "function"
  )
}

function clampScrollTop(scroller: HTMLElement, top: number): number {
  return Math.max(
    0,
    Math.min(top, scroller.scrollHeight - scroller.clientHeight)
  )
}

// How long to keep re-asserting a scroll target after the initial jump.
const SCROLL_HOLD_TIMEOUT_MS = 700

// Jump the scroller to getTarget() instantly, then re-assert that target each
// time the scroll content reflows (off-screen cards mounting, files expanding,
// annotation cards measuring) — a ResizeObserver is the real "layout settled"
// signal, replacing fixed frame-budget correction loops. Bails the moment the
// user scrolls so we never fight them, and disconnects after a short ceiling.
function jumpAndHold(
  scroller: HTMLElement,
  getTarget: () => number,
  timeout = SCROLL_HOLD_TIMEOUT_MS
): () => void {
  let raf = 0
  let stopped = false
  let timer = 0
  let ro: ResizeObserver | null = null
  const stop = () => {
    if (stopped) return
    stopped = true
    ro?.disconnect()
    if (raf) cancelAnimationFrame(raf)
    scroller.removeEventListener("wheel", stop)
    scroller.removeEventListener("touchstart", stop)
    window.clearTimeout(timer)
  }
  const reassert = () => {
    raf = 0
    if (stopped) return
    const desired = getTarget()
    if (Math.abs(desired - scroller.scrollTop) > 1) {
      scroller.scrollTo({ top: desired, behavior: "auto" })
    }
  }
  const schedule = () => {
    if (!raf && !stopped) raf = requestAnimationFrame(reassert)
  }
  scroller.scrollTo({ top: getTarget(), behavior: "auto" })
  ro = new ResizeObserver(schedule)
  ro.observe(scroller.firstElementChild ?? scroller)
  scroller.addEventListener("wheel", stop, { passive: true })
  scroller.addEventListener("touchstart", stop, { passive: true })
  timer = window.setTimeout(stop, timeout)
  return stop
}

// Absolute scrollTop that centers el within the scroller's viewport.
function elementCenterTarget(el: HTMLElement, scroller: HTMLElement): number {
  const elementRect = el.getBoundingClientRect()
  const scrollerRect = scroller.getBoundingClientRect()
  const delta =
    elementRect.top -
    scrollerRect.top -
    (scroller.clientHeight - elementRect.height) / 2
  return clampScrollTop(scroller, scroller.scrollTop + delta)
}

function scrollElementToCenter(el: HTMLElement, scroller: HTMLElement): number {
  const before = scroller.scrollTop
  const targetTop = elementCenterTarget(el, scroller)
  scroller.scrollTo({ top: targetTop, behavior: "auto" })
  return Math.abs(targetTop - before)
}

function scrollDiffLineToCenter(
  target: RegisteredDiffInstance,
  lineNumber: number,
  side: SelectionSide,
  scroller: HTMLElement
): boolean {
  if (!hasLinePosition(target.instance)) return false
  const line = target.instance.getLinePosition(lineNumber, side)
  if (!line) return false
  const hostTop =
    target.host.getBoundingClientRect().top -
    scroller.getBoundingClientRect().top +
    scroller.scrollTop
  const targetTop = clampScrollTop(
    scroller,
    hostTop + line.top - (scroller.clientHeight - line.height) / 2
  )
  scroller.scrollTo({ top: targetTop, behavior: "auto" })
  return true
}

/** A file as one walkthrough step shows it: only that step's hunks, unless `fileDiff` is null. */
interface ResolvedGroupFile {
  file: ReviewDiffFile
  fileDiff: FileDiffMetadata | null
  additions: number
  deletions: number
  /** First step the file appears in; scroll targets and diff instances register here. */
  primary: boolean
}

interface ResolvedGroup {
  index: number
  title: string
  summary: string
  other: boolean
  files: Array<ResolvedGroupFile>
  additions: number
  deletions: number
}

function wholeGroupFile(
  file: ReviewDiffFile,
  primary: boolean
): ResolvedGroupFile {
  return {
    file,
    fileDiff: null,
    additions: file.additions,
    deletions: file.deletions,
    primary,
  }
}

function sumStats(files: Array<ResolvedGroupFile>) {
  return {
    additions: files.reduce((acc, entry) => acc + entry.additions, 0),
    deletions: files.reduce((acc, entry) => acc + entry.deletions, 0),
  }
}

const GROUP_STYLES = {
  bug: { label: "Bug", className: "text-risk", glyph: Bug },
  investigate: {
    label: "Investigate",
    className: "text-attention",
    glyph: Flag,
  },
  informational: {
    label: "Informational",
    className: "text-ink-subtle",
    glyph: Info,
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

// Inline findings live inside Pierre's diff via React portals, so their
// expand/collapse state is lifted here and shared through context — surviving
// the annotation's mount/unmount as rows window in and out under
// virtualization, and letting the side panel drive the same expansion.
interface ExpandedFindingContextValue {
  expandedId: string | null
  reviewUrl: string
  toggle: (finding: ReviewFinding) => void
  registerAnnotation: (id: string, node: HTMLElement | null) => void
}

const ExpandedFindingContext =
  createContext<ExpandedFindingContextValue | null>(null)

function useExpandedFinding(): ExpandedFindingContextValue {
  const ctx = useContext(ExpandedFindingContext)
  if (!ctx)
    throw new Error("useExpandedFinding must be used within its provider")
  return ctx
}

const NO_FINDINGS: Array<ReviewFinding> = []
const NO_CHAT_DRAFTS: ReadonlyArray<ProposedComment> = []
const NO_PENDING_COMMENTS: ReadonlyArray<PendingReviewComment> = []
const NO_PATHS: ReadonlySet<string> = new Set()
const ignoreSection = (_path: string, _node: HTMLDivElement | null) => {}

/** Scroll to a line in whichever registered slice of the file renders it. */
function scrollSlicesLineToCenter(
  slices: Map<string, RegisteredDiffInstance>,
  lineNumber: number,
  side: SelectionSide,
  scroller: HTMLElement
): boolean {
  for (const target of slices.values()) {
    if (scrollDiffLineToCenter(target, lineNumber, side, scroller)) return true
  }
  return false
}

interface UserSelection {
  file: string
  range: SelectedLineRange
}

export type ReviewMainBodyVariant = "full" | "embedded"

export interface ReviewMainBodyProps {
  detail: ReviewDetail
  diffFiles: Array<ReviewDiffFile> | null
  // "full" renders the side panel + chat alongside the diffs; "embedded" renders
  // just the main body with an expand affordance (used inside the git panel).
  variant?: ReviewMainBodyVariant
  onExpand?: () => void
  // A PR comment opened from the comments dropdown: shown inline at its line.
  openComment?: PrReviewComment | null
  onUpdateOpenComment?: (comment: PrReviewComment) => void
  onCloseOpenComment?: () => void
}

export function ReviewMainBody({
  detail,
  diffFiles,
  variant = "full",
  onExpand,
  openComment,
  onUpdateOpenComment,
  onCloseOpenComment,
}: ReviewMainBodyProps) {
  // The composer provider lives here so it remounts in lockstep with the
  // head_sha-keyed body (and the activeId-keyed chat thread). The embedded
  // variant has no chat, so it skips the provider.
  if (variant === "embedded") {
    return (
      <ReviewBodyInner
        detail={detail}
        diffFiles={diffFiles}
        variant="embedded"
        onExpand={onExpand}
      />
    )
  }
  return (
    <ReviewChatComposerProvider>
      <ReviewBodyInner
        detail={detail}
        diffFiles={diffFiles}
        variant="full"
        openComment={openComment ?? null}
        onUpdateOpenComment={onUpdateOpenComment}
        onCloseOpenComment={onCloseOpenComment}
      />
    </ReviewChatComposerProvider>
  )
}

function ReviewBodyInner({
  detail,
  diffFiles,
  variant,
  onExpand,
  openComment = null,
  onUpdateOpenComment,
  onCloseOpenComment,
}: {
  detail: ReviewDetail
  diffFiles: Array<ReviewDiffFile> | null
  variant: ReviewMainBodyVariant
  onExpand?: () => void
  openComment?: PrReviewComment | null
  onUpdateOpenComment?: (comment: PrReviewComment) => void
  onCloseOpenComment?: () => void
}) {
  const embedded = variant === "embedded"
  const composer = useReviewChatComposer()
  const transformPrImage = useCallback(
    (src: string) =>
      reviewImageProxyUrl(detail.owner, detail.repo, detail.number, src),
    [detail.owner, detail.repo, detail.number]
  )
  const [sideTab, setSideTab] = useState<SideTab>("info")
  const [sidePanelOpen, setSidePanelOpen] = useState(false)
  const wide = useMediaQuery(WIDE_MEDIA_QUERY)
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const fileRefs = useRef<Record<string, HTMLDivElement | null>>({})
  // Per path, one instance per rendered slice: a file split across walkthrough
  // steps renders once per step, each showing only that step's hunks.
  const diffInstanceRefs = useRef<
    Record<string, Map<string, RegisteredDiffInstance> | undefined>
  >({})
  const annotationRefs = useRef<Record<string, HTMLElement | null>>({})
  const [expandedFiles, setExpandedFiles] = useState<Record<string, boolean>>(
    {}
  )
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [userSelection, setUserSelection] = useState<UserSelection | null>(null)
  // The single open inline comment composer (at most one across all files).
  const [commentDraft, setCommentDraft] = useState<{
    file: string
    range: SelectedLineRange
  } | null>(null)
  const diffScrollElRef = useRef<HTMLDivElement | null>(null)
  const virtualizerRef = useRef<DiffVirtualizer | null>(null)
  const findingScrollRequestRef = useRef(0)
  // Cancels the in-flight scroll "hold" (see jumpAndHold) when a new navigation
  // begins or the component unmounts, so holds never fight each other.
  const scrollHoldStopRef = useRef<(() => void) | null>(null)
  const groupRefs = useRef<Record<number, HTMLDivElement | null>>({})
  // The block pinned at the top of the diff (scroll-spy), highlighted in the
  // agenda sidebar.
  const [activeGroup, setActiveGroup] = useState<number | null>(null)
  const [diffStyle, setDiffStyleState] = useState<DiffStyle>(() =>
    readStoredDiffStyle()
  )
  const setDiffStyle = useCallback((next: DiffStyle) => {
    setDiffStyleState(next)
    if (typeof window !== "undefined") {
      window.localStorage.setItem(REVIEW_DIFF_STYLE_STORAGE_KEY, next)
    }
  }, [])

  useEffect(() => {
    void warmDiffHighlighter()
  }, [])

  // Latest-value refs so the callbacks below can stay referentially stable
  // (so memo(FileDiffCard) actually skips unrelated re-renders) while still
  // reading current state.
  const expandedFinding = useMemo(
    () => detail.findings.find((f) => f.id === expandedId) ?? null,
    [detail.findings, expandedId]
  )
  const expandedFindingRef = useRef(expandedFinding)
  useEffect(() => {
    expandedFindingRef.current = expandedFinding
  }, [expandedFinding])

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
  const viewedRef = useRef(viewed)
  const expandedRef = useRef(expandedFiles)
  useEffect(() => {
    viewedRef.current = viewed
    expandedRef.current = expandedFiles
  }, [viewed, expandedFiles])

  const toggleViewed = useCallback(
    (path: string) => {
      const becomingViewed = !viewedRef.current.has(path)
      setViewed((prev) => {
        const next = new Set(prev)
        if (becomingViewed) next.add(path)
        else next.delete(path)
        window.localStorage.setItem(
          viewedStorageKey,
          JSON.stringify(Array.from(next))
        )
        return next
      })
      if (becomingViewed && expandedFindingRef.current?.file === path)
        setExpandedId(null)
      setExpandedFiles((prev) => ({ ...prev, [path]: !becomingViewed }))
    },
    [viewedStorageKey]
  )

  const readStorageKey = `open-swe.review.read.${detail.thread_id ?? `${detail.owner}/${detail.repo}/${detail.number}`}`
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
      window.localStorage.setItem(
        readStorageKey,
        JSON.stringify(Array.from(next))
      )
    },
    [readStorageKey]
  )
  const markRead = useCallback(
    (id: string) => {
      setRead((prev) => {
        const next = new Set(prev).add(id)
        persistRead(next)
        return next
      })
    },
    [persistRead]
  )
  const markAllRead = useCallback(() => {
    const next = new Set(detail.findings.map((f) => f.id))
    setRead(next)
    persistRead(next)
  }, [detail.findings, persistRead])

  const pendingReview = usePendingReview(
    detail.owner,
    detail.repo,
    detail.number
  )
  const pendingByFile = useMemo(() => {
    const byFile = new Map<string, Array<PendingReviewComment>>()
    for (const comment of pendingReview.comments) {
      if (comment.line === null) continue
      const list = byFile.get(comment.path) ?? []
      list.push(comment)
      byFile.set(comment.path, list)
    }
    return byFile
  }, [pendingReview.comments])
  const chatDraftStore = useChatDrafts()
  const chatDraftsByFile = useMemo(() => {
    const byFile = new Map<string, Array<ProposedComment>>()
    for (const draft of chatDraftStore?.comments ?? []) {
      if (draft.outcome) continue
      const list = byFile.get(draft.proposal.range.file) ?? []
      list.push(draft.proposal)
      byFile.set(draft.proposal.range.file, list)
    }
    return byFile
  }, [chatDraftStore?.comments])

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

  // Resolve the scout's steps against the actual diff: each step shows only
  // the hunks holding its lines, and any file no step claims joins the "Other"
  // step so nothing ever disappears.
  const walkthrough = detail.walkthrough
  const groupedView = useMemo<Array<ResolvedGroup> | null>(() => {
    if (!diffFiles || !walkthrough || walkthrough.steps.length === 0)
      return null
    const byPath = new Map(diffFiles.map((file) => [file.path, file]))
    const seen = new Set<string>()
    const resolved: Array<Omit<ResolvedGroup, "index">> = []
    for (const step of walkthrough.steps) {
      const files: Array<ResolvedGroupFile> = []
      for (const lines of step.files) {
        const file = byPath.get(lines.path)
        if (!file) continue
        const primary = !seen.has(file.path)
        seen.add(file.path)
        const hasLines = lines.added.length > 0 || lines.deleted.length > 0
        files.push(
          hasLines
            ? {
                file,
                fileDiff: walkthroughFileDiff(file, lines),
                additions: rangeLineCount(lines.added),
                deletions: rangeLineCount(lines.deleted),
                primary,
              }
            : wholeGroupFile(file, primary)
        )
      }
      if (files.length === 0) continue
      resolved.push({
        title: step.title,
        summary: step.other ? "" : step.summary,
        other: step.other,
        files,
        ...sumStats(files),
      })
    }
    const leftover = diffFiles
      .filter((file) => !seen.has(file.path))
      .map((file) => wholeGroupFile(file, true))
    if (leftover.length > 0) {
      const other = resolved.find((group) => group.other)
      if (other) {
        other.files = [...other.files, ...leftover]
        Object.assign(other, sumStats(other.files))
      } else {
        resolved.push({
          title: "Other changes",
          summary: "",
          other: true,
          files: leftover,
          ...sumStats(leftover),
        })
      }
    }
    if (resolved.length === 0) return null
    return resolved.map((group, i) => ({ ...group, index: i + 1 }))
  }, [diffFiles, walkthrough])

  const sidebarGroups = useMemo<Array<ReviewSidebarGroup> | null>(() => {
    if (!groupedView) return null
    return groupedView.map((group) => ({
      index: group.index,
      title: group.title,
    }))
  }, [groupedView])

  // The view follows fresh-group availability until the user explicitly picks
  // one, after which the choice persists across PRs.
  const hasFreshGroups = (walkthrough?.steps.length ?? 0) > 0
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

  const collapsedByDefault = useMemo<ReadonlySet<string>>(() => {
    if (view !== "ai" || !groupedView) return NO_PATHS
    const paths = (groups: Array<ResolvedGroup>) =>
      groups.flatMap((group) => group.files.map((entry) => entry.file.path))
    const inSteps = new Set(paths(groupedView.filter((group) => !group.other)))
    return new Set(
      paths(groupedView.filter((group) => group.other)).filter(
        (path) => !inSteps.has(path)
      )
    )
  }, [view, groupedView])
  const collapsedByDefaultRef = useRef(collapsedByDefault)
  useEffect(() => {
    collapsedByDefaultRef.current = collapsedByDefault
  }, [collapsedByDefault])

  const scrollToFile = useCallback((path: string) => {
    setSelectedFile(path)
    setExpandedFiles((prev) => ({ ...prev, [path]: true }))
    scrollHoldStopRef.current?.()
    requestAnimationFrame(() => {
      const el = fileRefs.current[path]
      const scroller = diffScrollElRef.current
      if (!el || !scroller) return
      scrollHoldStopRef.current = virtualizerRef.current
        ? scrollCardToTopVirtual(el, scroller, virtualizerRef.current)
        : scrollCardToTop(el, scroller)
    })
  }, [])

  const scrollToGroup = useCallback((index: number) => {
    scrollHoldStopRef.current?.()
    requestAnimationFrame(() => {
      const el = groupRefs.current[index]
      const scroller = diffScrollElRef.current
      if (!el || !scroller) return
      scrollHoldStopRef.current = virtualizerRef.current
        ? scrollCardToTopVirtual(el, scroller, virtualizerRef.current)
        : scrollCardToTop(el, scroller)
    })
  }, [])

  const scrollToTop = useCallback(() => {
    scrollHoldStopRef.current?.()
    const scroller = diffScrollElRef.current
    if (scroller) scrollHoldStopRef.current = jumpAndHold(scroller, () => 0)
  }, [])

  useEffect(() => () => scrollHoldStopRef.current?.(), [])

  // Scroll-spy: track which block's header is currently pinned at the top of the
  // diff scroller and surface it as the active agenda row (Google-Docs outline).
  useEffect(() => {
    if (view !== "ai" || !groupedView || groupedView.length === 0) {
      // oxlint-disable-next-line react/set-state-in-effect
      setActiveGroup(null)
      return
    }
    const scroller = diffScrollElRef.current
    if (!scroller) return
    let raf = 0
    const compute = () => {
      raf = 0
      const top = scroller.getBoundingClientRect().top
      let current = groupedView[0]?.index ?? null
      for (const group of groupedView) {
        const el = groupRefs.current[group.index]
        if (!el) continue
        if (el.getBoundingClientRect().top - top <= SCROLL_TOP_GAP + 2)
          current = group.index
        else break
      }
      setActiveGroup(current)
    }
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(compute)
    }
    compute()
    scroller.addEventListener("scroll", onScroll, { passive: true })
    return () => {
      scroller.removeEventListener("scroll", onScroll)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [view, groupedView])

  const filesByPath = useMemo(
    () => new Map((diffFiles ?? []).map((file) => [file.path, file])),
    [diffFiles]
  )
  const filesByPathRef = useRef(filesByPath)
  useEffect(() => {
    filesByPathRef.current = filesByPath
  }, [filesByPath])

  // The Virtualizer doesn't forward a ref; grab its scroll element (the
  // grandparent of this hidden probe, which lives in its content div) so
  // scroll-to-file/group can align against it.
  const scrollerProbe = useCallback((node: HTMLDivElement | null) => {
    const scroller = node?.parentElement?.parentElement
    diffScrollElRef.current =
      scroller instanceof HTMLDivElement ? scroller : null
  }, [])

  const registerSection = useCallback(
    (path: string, node: HTMLDivElement | null) => {
      fileRefs.current[path] = node
    },
    []
  )
  const registerAnnotation = useCallback(
    (id: string, node: HTMLElement | null) => {
      annotationRefs.current[id] = node
    },
    []
  )
  const registerDiffInstance = useCallback(
    (path: string, slice: string, target: RegisteredDiffInstance | null) => {
      const slices = diffInstanceRefs.current[path] ?? new Map()
      if (target) slices.set(slice, target)
      else slices.delete(slice)
      if (slices.size > 0) diffInstanceRefs.current[path] = slices
      else delete diffInstanceRefs.current[path]
    },
    []
  )

  const toggleExpanded = useCallback((path: string) => {
    const current =
      expandedRef.current[path] ??
      (!viewedRef.current.has(path) && !collapsedByDefaultRef.current.has(path))
    const next = !current
    if (!next && expandedFindingRef.current?.file === path) setExpandedId(null)
    setExpandedFiles((prev) => ({ ...prev, [path]: next }))
  }, [])

  const selectLines = useCallback(
    (path: string, range: SelectedLineRange | null) => {
      if (range) {
        setUserSelection({ file: path, range })
        if (expandedFindingRef.current) setExpandedId(null)
      } else {
        setUserSelection((prev) => (prev?.file === path ? null : prev))
      }
    },
    []
  )

  const addToChat = useCallback(
    async (path: string, range: SelectedLineRange) => {
      const file = filesByPathRef.current.get(path)
      if (!file) return
      setSideTab("chat")
      setSidePanelOpen(true)
      try {
        const contents = await loadReviewFileContents(
          detail.owner,
          detail.repo,
          detail.number,
          file
        )
        for (const excerpt of selectionExcerpts(file.path, contents, range)) {
          composer?.addAttachment({ id: crypto.randomUUID(), ...excerpt })
        }
        setUserSelection(null)
      } catch (error) {
        setUserSelection({ file: path, range })
        toast.error("Couldn’t add selection to chat", {
          description:
            error instanceof Error
              ? error.message
              : "Failed to load file contents",
        })
      }
    },
    [composer, detail.owner, detail.repo, detail.number]
  )

  // Open the inline comment composer for a line (gutter "+" click). Clearing the
  // chat selection + expanded finding keeps the "+" owned by the composer alone.
  const startComment = useCallback((path: string, range: SelectedLineRange) => {
    setUserSelection(null)
    setExpandedId(null)
    setCommentDraft({ file: path, range })
  }, [])
  const closeComment = useCallback(() => setCommentDraft(null), [])

  // ⌘L / Ctrl+L adds the current line selection to the chat (Cursor-style).
  const userSelectionRef = useRef(userSelection)
  useEffect(() => {
    userSelectionRef.current = userSelection
  }, [userSelection])
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "l") {
        const sel = userSelectionRef.current
        if (sel) {
          event.preventDefault()
          addToChat(sel.file, sel.range)
        }
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [addToChat])

  // Clicking away from the highlighted rows clears the selection. A pointer-down
  // that begins a fresh selection clears here first, then the new drag repaints.
  // Reads the ref so the listener is registered once (no churn during a drag).
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (!userSelectionRef.current) return
      const target = event.target
      if (target instanceof Element && target.closest("[data-add-to-chat]"))
        return
      setUserSelection(null)
    }
    window.addEventListener("pointerdown", onPointerDown)
    return () => window.removeEventListener("pointerdown", onPointerDown)
  }, [])

  // Toggle a finding from its in-diff header — it's already on-screen, so no
  // scrolling is needed.
  const toggleInline = useCallback(
    (finding: ReviewFinding) => {
      markRead(finding.id)
      setExpandedId((prev) => (prev === finding.id ? null : finding.id))
    },
    [markRead]
  )

  // Open a finding from the side panel. Anchored findings expand inline in the
  // diff: open the file, scroll it into view, then poll a few frames for the
  // annotation node (its diff rows window in/out under virtualization) and
  // scroll that into view. Non-anchored findings expand inline in the panel.
  const openFromPanel = useCallback(
    (finding: ReviewFinding) => {
      markRead(finding.id)
      const willExpand = expandedFindingRef.current?.id !== finding.id
      const requestId = ++findingScrollRequestRef.current
      setUserSelection(null)
      setExpandedId(willExpand ? finding.id : null)
      if (!willExpand || !isAnchored(finding)) return
      setSelectedFile(finding.file)
      setExpandedFiles((prev) => ({ ...prev, [finding.file]: true }))
      scrollHoldStopRef.current?.()
      let frames = 0
      let lineScrollDone = false
      const snap = () => {
        if (requestId !== findingScrollRequestRef.current) return
        const scroller = diffScrollElRef.current
        if (!scroller) return

        // Once the finding's inline card has mounted (its diff rows window in
        // under virtualization), center it and hold as the card settles.
        const annotation = annotationRefs.current[finding.id]
        if (annotation?.isConnected && annotation.getClientRects().length > 0) {
          scrollHoldStopRef.current = jumpAndHold(scroller, () =>
            elementCenterTarget(annotation, scroller)
          )
          return
        }

        const slices = diffInstanceRefs.current[finding.file]
        if (slices) {
          lineScrollDone =
            finding.end_line !== null &&
            scrollSlicesLineToCenter(
              slices,
              finding.end_line,
              findingSide(finding),
              scroller
            )
        } else if (!lineScrollDone) {
          const fileNode = fileRefs.current[finding.file]
          if (fileNode) scrollElementToCenter(fileNode, scroller)
        }

        if (frames++ < FINDING_SCROLL_MAX_FRAMES) requestAnimationFrame(snap)
      }
      requestAnimationFrame(snap)
    },
    [markRead]
  )

  // Open an existing PR comment inline: expand its file and scroll its line to
  // center (mirrors openFromPanel). Comments whose file/line aren't in the
  // current diff (e.g. outdated) have no inline anchor, so fall back to GitHub.
  const closeOpenCommentRef = useRef(onCloseOpenComment)
  useEffect(() => {
    closeOpenCommentRef.current = onCloseOpenComment
  }, [onCloseOpenComment])
  useEffect(() => {
    if (!openComment) return
    const { path, line } = openComment
    const fallbackToGitHub = () => {
      if (openComment.html_url) {
        window.open(openComment.html_url, "_blank", "noopener,noreferrer")
      }
      closeOpenCommentRef.current?.()
    }
    const file = filesByPathRef.current.get(path)
    // No inline anchor: the file isn't in the diff, the comment has no line, or
    // it's outdated (its line no longer appears in the current diff).
    if (!file || line === null || openComment.is_outdated) {
      fallbackToGitHub()
      return
    }
    setSelectedFile(path)
    setExpandedFiles((prev) => ({ ...prev, [path]: true }))
    scrollHoldStopRef.current?.()
    const requestId = ++findingScrollRequestRef.current
    const side: SelectionSide =
      openComment.side === "LEFT" ? "deletions" : "additions"
    const key = `comment:${openComment.id}`
    let frames = 0
    let lineScrollDone = false
    let mounted = false
    const snap = () => {
      if (requestId !== findingScrollRequestRef.current) return
      const scroller = diffScrollElRef.current
      if (!scroller) return
      const annotation = annotationRefs.current[key]
      if (annotation?.isConnected && annotation.getClientRects().length > 0) {
        mounted = true
        scrollHoldStopRef.current = jumpAndHold(scroller, () =>
          elementCenterTarget(annotation, scroller)
        )
        return
      }
      const slices = diffInstanceRefs.current[path]
      if (slices) {
        lineScrollDone = scrollSlicesLineToCenter(slices, line, side, scroller)
      } else if (!lineScrollDone) {
        const fileNode = fileRefs.current[path]
        if (fileNode) scrollElementToCenter(fileNode, scroller)
      }
      if (frames++ < FINDING_SCROLL_MAX_FRAMES) {
        requestAnimationFrame(snap)
      } else if (!mounted) {
        // The line never rendered (e.g. collapsed context) — fall back to GitHub
        // rather than leaving the menu closed with nothing shown.
        fallbackToGitHub()
      }
    }
    requestAnimationFrame(snap)
  }, [openComment])

  const [shownRange, setShownRange] = useState<{
    file: string
    range: SelectedLineRange
  } | null>(null)
  const pulseTimersRef = useRef<Array<number>>([])
  useEffect(
    () => () => pulseTimersRef.current.forEach((t) => window.clearTimeout(t)),
    []
  )
  const showRange = useCallback(
    (target: DiffRange) => {
      if (!filesByPathRef.current.has(target.file)) {
        console.warn("Chat asked to show a file that is not in this diff", {
          target,
        })
        toast.error(`${target.file} isn't in the diff loaded on this page`)
        return
      }
      if (!wide) setSidePanelOpen(false)
      const side: SelectionSide =
        target.side === "LEFT" ? "deletions" : "additions"
      const range: SelectedLineRange = {
        start: target.startLine,
        end: target.endLine,
        side,
        endSide: side,
      }
      setUserSelection(null)
      setExpandedId(null)
      setSelectedFile(target.file)
      setExpandedFiles((prev) => ({ ...prev, [target.file]: true }))
      scrollHoldStopRef.current?.()
      const requestId = ++findingScrollRequestRef.current
      let frames = 0
      const snap = () => {
        if (requestId !== findingScrollRequestRef.current) return
        const scroller = diffScrollElRef.current
        if (!scroller) return
        const slices = diffInstanceRefs.current[target.file]
        const done =
          !!slices &&
          scrollSlicesLineToCenter(slices, target.startLine, side, scroller)
        if (!done) {
          const fileNode = fileRefs.current[target.file]
          if (fileNode && frames === 0)
            scrollElementToCenter(fileNode, scroller)
          if (frames++ < FINDING_SCROLL_MAX_FRAMES) requestAnimationFrame(snap)
        }
      }
      requestAnimationFrame(snap)

      pulseTimersRef.current.forEach((t) => window.clearTimeout(t))
      const shown = { file: target.file, range }
      const steps: Array<[number, typeof shown | null]> = [
        [0, shown],
        [450, null],
        [700, shown],
        [1150, null],
        [1400, shown],
        [3400, null],
      ]
      pulseTimersRef.current = steps.map(([delay, value]) =>
        window.setTimeout(() => setShownRange(value), delay)
      )
    },
    [wide]
  )
  useEffect(() => {
    if (!composer) return
    composer.registerShowHandler(showRange)
    return () => composer.registerShowHandler(null)
  }, [composer, showRange])

  const renderFileCard = (
    file: ReviewDiffFile,
    step?: { index: number; entry: ResolvedGroupFile }
  ) => {
    const primary = step?.entry.primary ?? true
    // Keep the range highlighted while its comment composer is open, so the
    // user can see exactly which lines they're commenting on.
    const selectedLines =
      shownRange?.file === file.path
        ? shownRange.range
        : expandedFinding?.file === file.path && isAnchored(expandedFinding)
          ? findingSelectedRange(expandedFinding)
          : commentDraft?.file === file.path
            ? commentDraft.range
            : userSelection?.file === file.path
              ? userSelection.range
              : null
    return (
      <FileDiffCard
        key={step ? `${step.index}:${file.path}` : file.path}
        file={file}
        fileDiff={step?.entry.fileDiff ?? null}
        additions={step?.entry.additions ?? file.additions}
        deletions={step?.entry.deletions ?? file.deletions}
        findings={findingsByFile.get(file.path) ?? NO_FINDINGS}
        chatDrafts={
          embedded
            ? NO_CHAT_DRAFTS
            : (chatDraftsByFile.get(file.path) ?? NO_CHAT_DRAFTS)
        }
        pendingComments={
          embedded
            ? NO_PENDING_COMMENTS
            : (pendingByFile.get(file.path) ?? NO_PENDING_COMMENTS)
        }
        selectedLines={selectedLines}
        viewed={viewed.has(file.path)}
        onToggleViewed={toggleViewed}
        expanded={
          expandedFiles[file.path] ??
          (!viewed.has(file.path) && !collapsedByDefault.has(file.path))
        }
        onToggleExpanded={toggleExpanded}
        onSelectLines={selectLines}
        onAddToChat={embedded ? undefined : addToChat}
        registerSection={primary ? registerSection : ignoreSection}
        slice={step ? String(step.index) : "all"}
        belowStepHeader={step !== undefined}
        registerDiffInstance={registerDiffInstance}
        diffStyle={diffStyle}
        owner={detail.owner}
        repo={detail.repo}
        prNumber={detail.number}
        commentDraftRange={
          commentDraft?.file === file.path ? commentDraft.range : null
        }
        onStartComment={embedded ? undefined : startComment}
        onCloseComment={closeComment}
        openComment={openComment?.path === file.path ? openComment : null}
        onUpdateOpenComment={onUpdateOpenComment}
        onCloseOpenComment={onCloseOpenComment}
      />
    )
  }

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
      activeGroup,
      onSelectOverview: scrollToTop,
    }),
    [
      scrollToTop,
      detail.number,
      diffFiles,
      selectedFile,
      viewed,
      scrollToFile,
      sidebarGroups,
      view,
      setView,
      scrollToGroup,
      activeGroup,
    ]
  )

  useEffect(() => {
    if (!expandedId) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpandedId(null)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [expandedId])

  const expandedFindingCtx = useMemo<ExpandedFindingContextValue>(
    () => ({
      expandedId,
      reviewUrl: detail.url,
      toggle: toggleInline,
      registerAnnotation,
    }),
    [expandedId, detail.url, toggleInline, registerAnnotation]
  )

  const sidePanel = (layout: SidePanelLayout) => (
    <SidePanel
      layout={layout}
      detail={detail}
      tab={sideTab}
      onTabChange={setSideTab}
      read={read}
      expandedId={expandedId}
      onMarkAllRead={markAllRead}
      onFindingClick={openFromPanel}
    />
  )

  return (
    <ExpandedFindingContext.Provider value={expandedFindingCtx}>
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <main className="relative flex min-h-0 min-w-0 flex-1">
          {!embedded && (
            <Stack
              bg="sidebar"
              className="hidden w-72 shrink-0 border-r border-line lg:flex"
            >
              <ReviewSidebarPanel data={sidebarData} />
            </Stack>
          )}
          <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
            {embedded && (
              <Inline
                justify="end"
                className="h-row-data shrink-0 border-b border-line px-3"
              >
                <Button size="compact" variant="outline" onClick={onExpand}>
                  <Icon icon={ExternalLink} size="sm" />
                  Open full review
                </Button>
              </Inline>
            )}
            <WorkerPoolContextProvider
              poolOptions={DIFF_WORKER_POOL_OPTIONS}
              highlighterOptions={DIFF_WORKER_HIGHLIGHTER_OPTIONS}
            >
              <Virtualizer
                className="relative min-h-0 flex-1 overflow-y-auto"
                contentClassName={cn(
                  "mx-auto w-full px-6 py-6",
                  diffStyle === "split" ? "max-w-none" : "max-w-6xl"
                )}
                config={DIFF_VIRTUALIZER_CONFIG}
              >
                <VirtualizerBridge
                  probeRef={scrollerProbe}
                  instanceRef={virtualizerRef}
                />
                <PrHeader
                  url={detail.url}
                  title={detail.pr.title}
                  number={detail.number}
                  state={detail.pr.state}
                  headRef={detail.pr.head_ref}
                  baseRef={detail.pr.base_ref}
                  author={detail.pr.author?.login}
                  stats={{
                    changedFiles: detail.pr.changed_files,
                    additions: detail.pr.additions,
                    deletions: detail.pr.deletions,
                  }}
                >
                  {!embedded &&
                    (detail.pr.state === "open" ||
                      detail.pr.state === "draft") && (
                      <ReviewPageActions
                        owner={detail.owner}
                        repo={detail.repo}
                        number={detail.number}
                      />
                    )}
                </PrHeader>
                {!detail.walkthrough && detail.pr.changed_files > 0 && (
                  <WalkthroughCallout detail={detail} />
                )}
                {detail.assessment && (
                  <ReviewAssessmentCard
                    assessment={detail.assessment}
                    owner={detail.owner}
                    repo={detail.repo}
                    number={detail.number}
                    headSha={detail.pr.head_sha}
                  />
                )}
                <Box
                  bg="panel"
                  border="line"
                  radius="panel"
                  padding="lg"
                  className="mt-4"
                >
                  {detail.pr.body ? (
                    <Markdown
                      content={detail.pr.body}
                      transformImageUrl={transformPrImage}
                    />
                  ) : (
                    <p className="text-meta text-ink-subtle">
                      This PR has no description.
                    </p>
                  )}
                </Box>
                <HumanInputCard
                  summary={detail.walkthrough?.human_input ?? ""}
                  className="mt-4"
                />
                {!embedded && (
                  <ReviewConversation
                    owner={detail.owner}
                    repo={detail.repo}
                    number={detail.number}
                    className="mt-6"
                  />
                )}

                <div className="mt-6">
                  <Inline gap="md" justify="between" className="mb-3">
                    <Box
                      render={<h2 />}
                      className="text-title font-medium text-ink"
                    >
                      Changes
                    </Box>
                    <Inline gap="md">
                      {detail.walkthrough && (
                        <ScoutThreadLink
                          threadId={detail.walkthrough_scout_thread_id}
                        />
                      )}
                      {linesLeft !== null && (
                        <span className="text-meta text-ink-subtle">
                          {linesLeft === 0
                            ? "All lines reviewed"
                            : `${linesLeft} lines left`}
                        </span>
                      )}
                      {diffFiles && diffFiles.length > 0 && (
                        <Inline gap="xs">
                          <DiffWrapToggle className="size-6" />
                          <DiffStyleToggle
                            value={diffStyle}
                            onChange={setDiffStyle}
                          />
                        </Inline>
                      )}
                    </Inline>
                  </Inline>
                  {diffFiles && diffFiles.length < detail.pr.changed_files && (
                    <p className="mb-2 text-meta text-ink-subtle">
                      Showing {diffFiles.length} of {detail.pr.changed_files}{" "}
                      changed files.{" "}
                      <a
                        href={`${detail.url}/files`}
                        target="_blank"
                        rel="noreferrer"
                        className="underline underline-offset-2 hover:text-ink"
                      >
                        See every file on GitHub
                      </a>
                    </p>
                  )}
                  {!diffFiles ? (
                    <Skeleton className="h-64 w-full rounded-panel" />
                  ) : diffFiles.length === 0 ? (
                    <EmptyState icon={FileText} title="No diff available." />
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
                          {group.files.map((entry) =>
                            renderFileCard(entry.file, {
                              index: group.index,
                              entry,
                            })
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {diffFiles.map((file) => renderFileCard(file))}
                    </div>
                  )}
                </div>
              </Virtualizer>
            </WorkerPoolContextProvider>
          </div>
        </main>

        {!embedded &&
          (wide ? (
            sidePanel("inline")
          ) : (
            <Sheet open={sidePanelOpen} onOpenChange={setSidePanelOpen}>
              {!sidePanelOpen && (
                <Button
                  variant="outline"
                  className="fixed right-4 bottom-4 z-30 shadow-popup"
                  onClick={() => setSidePanelOpen(true)}
                >
                  <Icon icon={MessageSquare} size="md" />
                  Info &amp; chat
                </Button>
              )}
              <SheetContent side="right" className="p-0">
                {sidePanel("sheet")}
              </SheetContent>
            </Sheet>
          ))}
      </div>
    </ExpandedFindingContext.Provider>
  )
}

function ScoutProgressPreview({ progress }: { progress: ScoutProgress }) {
  const { recent } = progress
  return (
    <Stack gap="xs" className="text-meta text-ink-subtle">
      <p>
        {progress.steps} step{progress.steps === 1 ? "" : "s"} committed
      </p>
      {recent.length > 0 && (
        <Stack render={<ol />} gap="none" className="font-mono text-meta">
          {recent.map((action, index) => {
            const current = progress.running && index === recent.length - 1
            return (
              <li
                key={index}
                className={cn("flex min-w-0 gap-2", current && "text-ink")}
              >
                <span className="flex w-3.5 shrink-0 justify-center">
                  {current ? <Spinner size="sm" /> : "·"}
                </span>
                <span className="shrink-0">{action.tool}</span>
                {action.target && (
                  <span className="min-w-0 truncate">{action.target}</span>
                )}
              </li>
            )
          })}
        </Stack>
      )}
    </Stack>
  )
}

function ScoutThreadLink({
  threadId,
  className,
}: {
  threadId: string | null
  className?: string
}) {
  if (!threadId) return null
  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId }}
      className={cn(
        "inline-block text-meta text-ink-subtle underline-offset-2 hover:text-ink hover:underline",
        className
      )}
    >
      Open thread
    </Link>
  )
}

/** Runs the review scout alone, so the walkthrough exists without a full review. */
function WalkthroughCallout({ detail }: { detail: ReviewDetail }) {
  const qc = useQueryClient()
  const scout = useMutation({
    mutationFn: ({ owner, repo, number }: ReviewRef) =>
      api.runReviewScout(owner, repo, number),
    meta: { errorTitle: "Couldn't build walkthrough" },
    onSuccess: ({ started }, { owner, repo, number }) => {
      const queryKey = ["review", owner, repo, number]
      if (started)
        qc.setQueryData<ReviewDetail>(queryKey, (old) =>
          old ? { ...old, walkthrough_running: true } : old
        )
      void qc.invalidateQueries({ queryKey: agentThreadKeys.lists })
      void qc.invalidateQueries({ queryKey })
    },
  })
  const running = detail.walkthrough_running || scout.isPending
  // This card only renders while there is no walkthrough, so a scout that
  // stops running while it is still mounted ended without one.
  const failure = running ? null : detail.walkthrough_error
  const failureSummary = failure?.split("\n", 1)[0]?.slice(0, 300)
  const wasRunning = useRef(detail.walkthrough_running)
  useEffect(() => {
    if (wasRunning.current && !detail.walkthrough_running) {
      toast.error("The walkthrough failed to build", {
        description: failureSummary
          ? `The review scout crashed: ${failureSummary}`
          : "The review scout finished without producing steps. Try again, or check its review-scout run in LangSmith.",
      })
    }
    wasRunning.current = detail.walkthrough_running
  }, [detail.walkthrough_running, failureSummary])
  useEffect(() => {
    if (!failure) return
    console.error("Review scout failed", {
      pr: `${detail.owner}/${detail.repo}#${detail.number}`,
      headSha: detail.head_sha,
      scoutThreadId: detail.walkthrough_scout_thread_id,
      error: failure,
    })
  }, [
    failure,
    detail.owner,
    detail.repo,
    detail.number,
    detail.head_sha,
    detail.walkthrough_scout_thread_id,
  ])
  return (
    <ReviewBand
      className="mt-4"
      mark={
        <IconWell>
          <Icon icon={ListOrdered} size="sm" />
        </IconWell>
      }
      title={
        running ? "Building the walkthrough…" : "Read this PR step by step"
      }
      description={
        running
          ? "The review scout is ordering the changes into narrated steps. This takes a few minutes; the page updates on its own."
          : "The review scout orders the changes into narrated steps and moves mechanical edits to the end."
      }
      meta={
        running || failureSummary ? (
          <Stack gap="sm">
            {running && detail.walkthrough_progress && (
              <ScoutProgressPreview progress={detail.walkthrough_progress} />
            )}
            {failureSummary && (
              <Inline
                gap="xs"
                align="start"
                className="text-label break-words text-risk"
              >
                <Icon icon={AlertTriangle} size="sm" className="mt-0.5" />
                <span>Last attempt failed: {failureSummary}</span>
              </Inline>
            )}
            {(running || failure) && (
              <ScoutThreadLink threadId={detail.walkthrough_scout_thread_id} />
            )}
          </Stack>
        ) : undefined
      }
      actions={
        <Button onClick={() => scout.mutate(detail)} disabled={running}>
          {running ? (
            <Spinner size="md" />
          ) : (
            <Icon icon={ListOrdered} size="md" />
          )}
          {running ? "Building…" : "Build walkthrough"}
        </Button>
      }
    />
  )
}

function isDiffStyle(value: unknown): value is DiffStyle {
  return value === "unified" || value === "split"
}

function DiffStyleToggle({
  value,
  onChange,
}: {
  value: DiffStyle
  onChange: (value: DiffStyle) => void
}) {
  return (
    <ToggleGroup
      aria-label="Diff layout"
      value={[value]}
      onValueChange={(next: unknown[]) => {
        const chosen = next[0]
        if (isDiffStyle(chosen)) onChange(chosen)
      }}
      className="min-h-0"
    >
      <ToggleGroupItem
        value="unified"
        aria-label="Unified view"
        title="Unified view"
        className="size-6 px-0"
      >
        <Icon icon={Rows} size="sm" />
      </ToggleGroupItem>
      <ToggleGroupItem
        value="split"
        aria-label="Split view"
        title="Split view"
        className="size-6 px-0"
      >
        <Icon icon={Columns} size="sm" />
      </ToggleGroupItem>
    </ToggleGroup>
  )
}

// Grabs the virtualizer instance from context (only available inside
// <Virtualizer>) and lifts it to the parent ref so scroll-to can read accurate
// offsets. Doubles as the hidden scroll-element probe.
function VirtualizerBridge({
  probeRef,
  instanceRef,
}: {
  probeRef: (node: HTMLDivElement | null) => void
  instanceRef: React.MutableRefObject<DiffVirtualizer | null>
}) {
  const virtualizer = useVirtualizer()
  useEffect(() => {
    instanceRef.current = virtualizer ?? null
  }, [virtualizer, instanceRef])
  return <div ref={probeRef} aria-hidden className="hidden" />
}

// Only the title row is pinned, stacked above Pierre's in-diff sticky header
// (z-index 4); the description scrolls with the page.
function GroupHeader({ group }: { group: ResolvedGroup }) {
  const title = useMemo(() => renderInlineCode(group.title), [group.title])
  const summary = useMemo(
    () => (group.summary ? stripLocationLinks(group.summary) : ""),
    [group.summary]
  )
  return (
    <>
      <Inline
        gap="sm"
        bg="canvas"
        className="sticky top-0 z-5 h-row-data border-b border-line"
      >
        <Box
          render={<span />}
          radius="badge"
          bg="muted"
          className="flex size-6 shrink-0 items-center justify-center font-mono text-meta font-medium text-ink-subtle"
        >
          {group.index}
        </Box>
        <Box
          render={<h3 />}
          className="min-w-0 flex-1 truncate text-title font-medium text-ink"
        >
          {title}
        </Box>
        <Inline gap="sm" className="shrink-0 font-mono text-meta tabular-nums">
          {group.additions > 0 && (
            <span className="text-positive">+{group.additions}</span>
          )}
          {group.deletions > 0 && (
            <span className="text-risk">−{group.deletions}</span>
          )}
        </Inline>
      </Inline>
      {summary && (
        <Box className="text-body text-ink-subtle">
          <Markdown content={summary} />
        </Box>
      )}
    </>
  )
}

const FileDiffCard = memo(function FileDiffCard({
  file,
  fileDiff,
  slice,
  belowStepHeader,
  additions,
  deletions,
  findings,
  selectedLines,
  viewed,
  onToggleViewed,
  expanded,
  onToggleExpanded,
  onSelectLines,
  onAddToChat,
  registerSection,
  registerDiffInstance,
  diffStyle,
  owner,
  repo,
  prNumber,
  commentDraftRange,
  onStartComment,
  onCloseComment,
  openComment,
  onUpdateOpenComment,
  onCloseOpenComment,
  chatDrafts,
  pendingComments,
}: {
  file: ReviewDiffFile
  /** A walkthrough step's slice of the file; `null` renders the whole diff. */
  fileDiff: FileDiffMetadata | null
  additions: number
  deletions: number
  findings: Array<ReviewFinding>
  selectedLines: SelectedLineRange | null
  viewed: boolean
  onToggleViewed: (path: string) => void
  expanded: boolean
  onToggleExpanded: (path: string) => void
  onSelectLines: (path: string, range: SelectedLineRange | null) => void
  onAddToChat?: (path: string, range: SelectedLineRange) => void
  registerSection: (path: string, node: HTMLDivElement | null) => void
  /** The step's pinned title sits above, so the file name pins just below it. */
  belowStepHeader: boolean
  /** Which rendering of the file this card is, when a walkthrough splits it. */
  slice: string
  registerDiffInstance: (
    path: string,
    slice: string,
    target: RegisteredDiffInstance | null
  ) => void
  diffStyle: DiffStyle
  owner: string
  repo: string
  prNumber: number
  commentDraftRange: SelectedLineRange | null
  onStartComment?: (path: string, range: SelectedLineRange) => void
  onCloseComment: () => void
  openComment: PrReviewComment | null
  onUpdateOpenComment?: (comment: PrReviewComment) => void
  onCloseOpenComment?: () => void
  /** Chat-drafted comments on this file still awaiting the user's decision. */
  chatDrafts: ReadonlyArray<ProposedComment>
  /** This file's comments in the viewer's pending GitHub review. */
  pendingComments: ReadonlyArray<PendingReviewComment>
}) {
  const diffOptions = useDiffOptions(diffStyle)
  const selectFileLines = useCallback(
    (range: SelectedLineRange | null) => onSelectLines(file.path, range),
    [onSelectLines, file.path]
  )
  // Line-number drags and the gutter "+" comment; text highlights add to chat.
  const routeSelection = useCallback(
    ({ range, source }: CommittedDiffSelection) => {
      if (source === "lines") onStartComment?.(file.path, range)
    },
    [onStartComment, file.path]
  )
  // No chat means no line selection or commenting (embedded view).
  const lineSelection = useDiffLineSelection({
    enabled: Boolean(onAddToChat && onStartComment),
    selectedLines,
    onSelectedLinesChange: selectFileLines,
    onCommit: routeSelection,
  })

  const findingAnnotations = useMemo<
    Array<DiffLineAnnotation<ReviewAnnotation>>
  >(
    () =>
      findings
        .filter((finding) => finding.end_line !== null)
        .map((finding) => ({
          side: findingSide(finding),
          lineNumber: finding.end_line as number,
          metadata: { kind: "finding", finding },
        })),
    [findings]
  )

  // The open draft composer and an opened existing comment each render inline as
  // one more annotation, anchored to their line on the appropriate side.
  const lineAnnotations = useMemo<
    Array<DiffLineAnnotation<ReviewAnnotation>>
  >(() => {
    const extra: Array<DiffLineAnnotation<ReviewAnnotation>> = []
    if (commentDraftRange) {
      extra.push({
        side:
          commentDraftRange.endSide ?? commentDraftRange.side ?? "additions",
        lineNumber: commentDraftRange.end,
        metadata: {
          kind: "draftComment",
          path: file.path,
          range: commentDraftRange,
        },
      })
    }
    if (openComment && openComment.line !== null) {
      extra.push({
        side: openComment.side === "LEFT" ? "deletions" : "additions",
        lineNumber: openComment.line,
        metadata: { kind: "comment", comment: openComment },
      })
    }
    for (const draft of chatDrafts) {
      extra.push({
        side: draft.range.side === "LEFT" ? "deletions" : "additions",
        lineNumber: draft.range.endLine,
        metadata: { kind: "chatDraft", draft },
      })
    }
    for (const comment of pendingComments) {
      if (comment.line === null) continue
      extra.push({
        side: comment.side === "LEFT" ? "deletions" : "additions",
        lineNumber: comment.line,
        metadata: { kind: "pending", comment },
      })
    }
    return extra.length > 0
      ? [...findingAnnotations, ...extra]
      : findingAnnotations
  }, [
    findingAnnotations,
    commentDraftRange,
    openComment,
    chatDrafts,
    pendingComments,
    file.path,
  ])

  // Pierre calls this the first time a viewer expands context past the hunks
  // the patch carried, and upgrades the parsed diff in place.
  const loadDiffFiles = useCallback(async (): Promise<FileDiffLoadedFiles> => {
    const contents = await loadReviewFileContents(owner, repo, prNumber, file)
    const original = contents.originalContent ?? ""
    const modified = contents.modifiedContent ?? ""
    return {
      oldFile: {
        name: file.previousPath ?? file.path,
        contents: original,
        cacheKey: fileContentsCacheKey(file.path, "old", original),
      },
      newFile: {
        name: file.path,
        contents: modified,
        cacheKey: fileContentsCacheKey(file.path, "new", modified),
      },
    }
  }, [owner, repo, prNumber, file])

  const cardOptions = useMemo(
    () => ({
      ...diffOptions,
      loadDiffFiles,
      ...lineSelection.diffOptions,
      onPostRender: (
        node: HTMLElement,
        instance: CoreFileDiff<ReviewAnnotation>
      ) => registerDiffInstance(file.path, slice, { host: node, instance }),
    }),
    [
      diffOptions,
      loadDiffFiles,
      lineSelection.diffOptions,
      file.path,
      slice,
      registerDiffInstance,
    ]
  )

  const addSelectionToChat = () => {
    const committed = lineSelection.committed
    if (committed) onAddToChat?.(file.path, committed.range)
    lineSelection.close()
    // Clear the lingering native highlight once added.
    readDiffSelection(
      lineSelection.wrapperProps.ref.current?.querySelector("diffs-container")
    )?.removeAllRanges()
  }

  const sectionRef = useCallback(
    (node: HTMLDivElement | null) => registerSection(file.path, node),
    [registerSection, file.path]
  )
  useEffect(
    () => () => registerDiffInstance(file.path, slice, null),
    [file.path, slice, registerDiffInstance]
  )
  const renderAnnotation = useCallback(
    (annotation: DiffLineAnnotation<ReviewAnnotation>) => {
      const meta = annotation.metadata
      if (meta.kind === "finding")
        return <InlineFinding finding={meta.finding} />
      if (meta.kind === "pending")
        return (
          <PendingReviewCommentCard
            owner={owner}
            repo={repo}
            number={prNumber}
            comment={meta.comment}
          />
        )
      if (meta.kind === "chatDraft")
        return (
          <div className="p-2 font-sans">
            <ProposedCommentCard
              owner={owner}
              repo={repo}
              number={prNumber}
              id={meta.draft.id}
            />
          </div>
        )
      if (meta.kind === "comment")
        return (
          <InlineComment
            owner={owner}
            repo={repo}
            prNumber={prNumber}
            comment={meta.comment}
            onUpdate={onUpdateOpenComment}
            onClose={onCloseOpenComment ?? (() => undefined)}
          />
        )
      return (
        <CommentComposer
          owner={owner}
          repo={repo}
          prNumber={prNumber}
          path={meta.path}
          range={meta.range}
          onClose={onCloseComment}
        />
      )
    },
    [
      owner,
      repo,
      prNumber,
      onCloseComment,
      onUpdateOpenComment,
      onCloseOpenComment,
    ]
  )

  return (
    <div
      ref={sectionRef}
      className="scroll-mt-4 overflow-clip rounded-compact border border-line"
    >
      <Inline
        gap="sm"
        bg="hover"
        className={cn(
          "sticky z-5 min-h-row-data border-b border-line px-3 py-1.5 text-label",
          belowStepHeader ? "top-10" : "top-0"
        )}
      >
        <Inline
          render={
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => onToggleExpanded(file.path)}
            />
          }
          gap="sm"
          className="min-w-0 text-left"
        >
          <Icon
            icon={ChevronDown}
            size="sm"
            className={cn(
              "text-ink-subtle transition-transform duration-fast ease-out-quint motion-reduce:transition-none",
              !expanded && "-rotate-90"
            )}
          />
          <span className="truncate font-mono font-medium text-ink">
            {file.path}
          </span>
        </Inline>
        <Inline gap="sm" className="shrink-0 font-mono text-meta tabular-nums">
          <span className="text-positive">+{additions}</span>
          <span className="text-risk">−{deletions}</span>
        </Inline>
        {findings.length > 0 && (
          <Badge tier="quiet" tone="attention">
            <Icon icon={Flag} size="sm" />
            {findings.length}
          </Badge>
        )}
        <Inline
          render={<label />}
          gap="sm"
          className="ml-auto shrink-0 cursor-pointer text-meta text-ink-subtle"
        >
          Mark as viewed
          <Checkbox
            checked={viewed}
            onCheckedChange={() => onToggleViewed(file.path)}
          />
        </Inline>
      </Inline>
      {expanded &&
        (file.unrenderable || file.patch === null ? (
          <Box
            bg="panel"
            padding="lg"
            className="text-center text-meta text-ink-subtle"
          >
            Binary or large file — diff not shown.
          </Box>
        ) : (
          <div
            {...lineSelection.wrapperProps}
            className="overflow-x-auto bg-panel font-mono text-meta leading-5"
          >
            {fileDiff ? (
              <FileDiff<ReviewAnnotation>
                fileDiff={fileDiff}
                // Pierre's worker pool highlights partial diffs out of step with
                // the rendered window ("deletionLine and additionLine are null").
                disableWorkerPool
                options={cardOptions}
                metrics={DIFF_VIRTUAL_METRICS}
                lineAnnotations={lineAnnotations}
                selectedLines={selectedLines}
                renderAnnotation={renderAnnotation}
              />
            ) : (
              <PatchDiff<ReviewAnnotation>
                patch={file.patch}
                disableWorkerPool
                options={cardOptions}
                metrics={DIFF_VIRTUAL_METRICS}
                lineAnnotations={lineAnnotations}
                selectedLines={selectedLines}
                renderAnnotation={renderAnnotation}
              />
            )}
          </div>
        ))}
      <DiffSelectionPopover
        selection={lineSelection}
        open={lineSelection.committed?.source === "text" && !commentDraftRange}
        initialFocus={false}
        className="rounded-badge p-0"
      >
        <Button
          type="button"
          size="compact"
          variant="ghost"
          data-add-to-chat
          onClick={addSelectionToChat}
          className="font-sans"
        >
          Add to Chat
          <Shortcut keys={["mod", "L"]} />
        </Button>
      </DiffSelectionPopover>
    </div>
  )
})

type MarkdownAction =
  | "heading"
  | "bold"
  | "italic"
  | "quote"
  | "code"
  | "link"
  | "ul"
  | "ol"
  | "task"

interface EditState {
  value: string
  start: number
  end: number
}

// Wrap the current selection (or a placeholder when empty) with a marker, e.g.
// **bold**. Returns the new value and the selection to restore.
function wrapSelection(
  state: EditState,
  marker: string,
  placeholder: string
): EditState {
  const selected = state.value.slice(state.start, state.end) || placeholder
  const value =
    state.value.slice(0, state.start) +
    marker +
    selected +
    marker +
    state.value.slice(state.end)
  const start = state.start + marker.length
  return { value, start, end: start + selected.length }
}

// Prefix each line touched by the selection, e.g. "> " for quotes or "1. " for
// ordered lists (prefix is computed per line so numbering increments).
function prefixLines(
  state: EditState,
  prefix: (index: number) => string
): EditState {
  const lineStart = state.value.lastIndexOf("\n", state.start - 1) + 1
  const block = state.value.slice(lineStart, state.end)
  const prefixed = block
    .split("\n")
    .map((line, index) => prefix(index) + line)
    .join("\n")
  const value =
    state.value.slice(0, lineStart) + prefixed + state.value.slice(state.end)
  return { value, start: lineStart, end: lineStart + prefixed.length }
}

function applyMarkdownAction(
  state: EditState,
  action: MarkdownAction
): EditState {
  switch (action) {
    case "bold":
      return wrapSelection(state, "**", "bold text")
    case "italic":
      return wrapSelection(state, "_", "italic text")
    case "code":
      return wrapSelection(state, "`", "code")
    case "heading":
      return prefixLines(state, () => "### ")
    case "quote":
      return prefixLines(state, () => "> ")
    case "ul":
      return prefixLines(state, () => "- ")
    case "ol":
      return prefixLines(state, (index) => `${index + 1}. `)
    case "task":
      return prefixLines(state, () => "- [ ] ")
    case "link": {
      const text = state.value.slice(state.start, state.end) || "text"
      const inserted = `[${text}](url)`
      const value =
        state.value.slice(0, state.start) +
        inserted +
        state.value.slice(state.end)
      const urlStart = state.start + text.length + 3
      return { value, start: urlStart, end: urlStart + 3 }
    }
  }
}

interface ToolbarItem {
  action: MarkdownAction
  label: string
  glyph: Glyph
}

// Grouped to match GitHub's comment toolbar (format group, then list group).
const MARKDOWN_TOOLBAR: ReadonlyArray<ReadonlyArray<ToolbarItem>> = [
  [
    { action: "heading", label: "Heading", glyph: Heading },
    { action: "bold", label: "Bold", glyph: Bold },
    { action: "italic", label: "Italic", glyph: Italic },
    { action: "quote", label: "Quote", glyph: Quote },
    { action: "code", label: "Code", glyph: Code },
    { action: "link", label: "Link", glyph: LinkGlyph },
  ],
  [
    { action: "ul", label: "Bulleted list", glyph: List },
    { action: "ol", label: "Numbered list", glyph: ListOrdered },
    { action: "task", label: "Task list", glyph: ListChecks },
  ],
]

// The inline comment composer, opened by clicking the gutter "+" on a line.
// Rendered through the same Pierre annotation portal as InlineFinding, so it sits
// in place at the line. Mirrors GitHub's stock comment box (Write/Preview tabs +
// markdown toolbar); submitting posts a real PR review comment as the user.
function CommentComposer({
  owner,
  repo,
  prNumber,
  path,
  range,
  onClose,
}: {
  owner: string
  repo: string
  prNumber: number
  path: string
  range: SelectedLineRange
  onClose: () => void
}) {
  const [value, setValue] = useState("")
  const [mode, setMode] = useState<"write" | "preview">("write")
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const pending = usePendingReview(owner, repo, prNumber)
  const mutation = pending.add
  useEffect(() => {
    textareaRef.current?.focus()
  }, [])
  const submit = () => {
    const body = value.trim()
    if (!body || mutation.isPending) return
    mutation.mutate(buildCommentPayload(path, range, body), {
      onSuccess: onClose,
    })
  }
  // Apply a toolbar action to the live textarea selection, then restore the
  // caret/selection on the next frame (after the controlled value re-renders).
  const applyAction = (action: MarkdownAction) => {
    const textarea = textareaRef.current
    if (!textarea) return
    const next = applyMarkdownAction(
      { value, start: textarea.selectionStart, end: textarea.selectionEnd },
      action
    )
    setValue(next.value)
    requestAnimationFrame(() => {
      textarea.focus()
      textarea.setSelectionRange(next.start, next.end)
    })
  }
  return (
    <Box className="px-2 py-1 font-sans">
      <Stack
        bg="panel"
        border="line"
        radius="compact"
        className="overflow-hidden"
      >
        <Inline
          gap="sm"
          className="border-b border-line py-1 pr-1 pl-3 text-meta"
        >
          <Icon icon={MessageSquare} size="sm" className="text-ink-subtle" />
          <span className="font-medium text-ink">
            Add a comment on line {commentRangeLabel(range)}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Close comment"
            className="ml-auto"
            onClick={onClose}
          >
            <Icon icon={X} size="sm" />
          </Button>
        </Inline>
        <Inline gap="xs" className="border-b border-line px-1.5 py-1">
          <ToggleGroup
            aria-label="Comment mode"
            value={[mode]}
            onValueChange={(next: unknown[]) => {
              if (next[0] === "write" || next[0] === "preview") setMode(next[0])
            }}
            className="min-h-0"
          >
            <ToggleGroupItem value="write" className="h-6">
              Write
            </ToggleGroupItem>
            <ToggleGroupItem value="preview" className="h-6">
              Preview
            </ToggleGroupItem>
          </ToggleGroup>
          {mode === "write" && (
            <Inline gap="none" className="ml-auto">
              {MARKDOWN_TOOLBAR.map((group, groupIndex) => (
                <Fragment key={group[0]?.action ?? groupIndex}>
                  {groupIndex > 0 && (
                    <Box aria-hidden="true" className="mx-1 h-4 w-px bg-line" />
                  )}
                  {group.map(({ action, label, glyph }) => (
                    <Tooltip key={action}>
                      <TooltipTrigger
                        render={
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            aria-label={label}
                            onMouseDown={(event) => event.preventDefault()}
                            onClick={() => applyAction(action)}
                          />
                        }
                      >
                        <Icon icon={glyph} size="md" />
                      </TooltipTrigger>
                      <TooltipContent side="top">{label}</TooltipContent>
                    </Tooltip>
                  ))}
                </Fragment>
              ))}
            </Inline>
          )}
        </Inline>
        <Stack gap="sm" padding="sm">
          {mode === "write" ? (
            <Textarea
              ref={textareaRef}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault()
                  submit()
                } else if (event.key === "Escape") {
                  event.preventDefault()
                  onClose()
                }
              }}
              placeholder="Leave a comment…"
              rows={3}
              className="resize-y"
            />
          ) : (
            <Box
              bg="muted"
              border="line"
              radius="compact"
              className="min-h-16 px-3 py-2 text-label"
            >
              {value.trim() ? (
                <Markdown content={value} />
              ) : (
                <span className="text-ink-subtle">Nothing to preview</span>
              )}
            </Box>
          )}
          {mutation.isError && (
            <Inline
              role="alert"
              gap="xs"
              align="start"
              className="text-meta text-risk"
            >
              <Icon icon={AlertTriangle} size="sm" />
              <span>
                {mutation.error instanceof Error
                  ? mutation.error.message
                  : "Failed to add the comment"}
              </span>
            </Inline>
          )}
          <Inline gap="sm" justify="end">
            <Button
              type="button"
              size="compact"
              variant="ghost"
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              type="button"
              size="compact"
              onClick={submit}
              disabled={!value.trim()}
              loading={mutation.isPending}
            >
              Add review comment
            </Button>
          </Inline>
        </Stack>
      </Stack>
    </Box>
  )
}

// An existing PR comment opened from the comments dropdown, rendered inline at
// its line through the same annotation portal as InlineFinding. Read-only; the
// node is registered so the dropdown can scroll it into view. Links to the
// full thread on GitHub.
function InlineComment({
  owner,
  repo,
  prNumber,
  comment,
  onUpdate,
  onClose,
}: {
  owner: string
  repo: string
  prNumber: number
  comment: PrReviewComment
  onUpdate?: (comment: PrReviewComment) => void
  onClose: () => void
}) {
  const { registerAnnotation } = useExpandedFinding()
  const session = useSession()
  const queryClient = useQueryClient()
  const [body, setBody] = useState(comment.body)
  const [draft, setDraft] = useState(comment.body)
  const [editing, setEditing] = useState(false)
  const sideLabel = comment.side === "LEFT" ? "L" : "R"
  const editable =
    session.data?.login.toLowerCase() === comment.author.toLowerCase()
  const commentsKey = ["reviewComments", owner, repo, prNumber]
  const mutation = useMutation({
    mutationFn: ({ next }: { next: string; previous: string }) =>
      api.updateReviewComment(owner, repo, prNumber, comment.id, next),
    meta: { errorTitle: "Couldn't update comment" },
    onMutate: async ({ next }) => {
      setBody(next)
      setEditing(false)
      return {
        undo: await optimisticUpdate<ReviewCommentsPayload>(
          queryClient,
          commentsKey,
          (old) => ({
            comments: old.comments.map((c) =>
              c.id === comment.id ? { ...c, body: next } : c
            ),
          })
        ),
      }
    },
    onSuccess: (_, { next }) => {
      setDraft(next)
      onUpdate?.({ ...comment, body: next })
    },
    onError: (_error, { next, previous }, context) => {
      context?.undo()
      setBody(previous)
      setDraft(next)
      setEditing(true)
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: commentsKey })
    },
  })
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setBody(comment.body)
    setDraft(comment.body)
    setEditing(false)
  }, [comment.id, comment.body])
  const submit = () => {
    const next = draft.trim()
    if (next && next !== body && !mutation.isPending)
      mutation.mutate({ next, previous: body })
  }
  const cancel = () => {
    setDraft(body)
    setEditing(false)
    mutation.reset()
  }
  return (
    <div
      ref={(node) => registerAnnotation(`comment:${comment.id}`, node)}
      className="px-2 py-1 font-sans"
    >
      <Stack
        bg="panel"
        border="line"
        radius="compact"
        className="overflow-hidden"
      >
        <Inline
          gap="sm"
          className="border-b border-line py-1 pr-1 pl-3 text-meta"
        >
          <Avatar
            name={comment.author}
            src={comment.author_avatar_url ?? undefined}
            size="chat"
            className="size-5"
          />
          <span className="font-medium text-ink">{comment.author}</span>
          {comment.line !== null && (
            <span className="font-mono text-ink-subtle">
              {sideLabel}
              {comment.line}
            </span>
          )}
          <Inline gap="none" className="ml-auto">
            {editable && !editing && (
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label="Edit comment"
                onClick={() => setEditing(true)}
              >
                <Icon icon={Pencil} size="sm" />
              </Button>
            )}
            <a
              href={comment.html_url}
              target="_blank"
              rel="noreferrer"
              aria-label="View on GitHub"
              title="View on GitHub"
              className={cn(
                buttonVariants({ variant: "ghost", size: "icon-sm" }),
                "text-ink-subtle hover:text-ink"
              )}
            >
              <Icon icon={GitHub} size="sm" />
            </a>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="Close comment"
              onClick={onClose}
            >
              <Icon icon={X} size="sm" />
            </Button>
          </Inline>
        </Inline>
        {editing ? (
          <Stack gap="sm" padding="sm">
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault()
                  submit()
                } else if (event.key === "Escape") {
                  event.preventDefault()
                  cancel()
                }
              }}
              rows={3}
              className="resize-y"
              autoFocus
            />
            <Inline gap="sm" justify="end">
              <Button
                type="button"
                size="compact"
                variant="ghost"
                onClick={cancel}
              >
                Cancel
              </Button>
              <Button
                type="button"
                size="compact"
                onClick={submit}
                disabled={!draft.trim() || draft.trim() === body}
                loading={mutation.isPending}
              >
                Save
              </Button>
            </Inline>
          </Stack>
        ) : (
          <Box className="px-3 py-2.5 text-label text-ink">
            <Markdown content={body} />
          </Box>
        )}
      </Stack>
    </div>
  )
}

// The finding rendered inline in the diff (via Pierre's annotation portal). A
// collapsed header sits at the line; clicking it expands the full details in
// place. Expand state is shared through context so it survives the annotation
// remounting as rows window in/out, and so the side panel can drive it.
function InlineFinding({ finding }: { finding: ReviewFinding }) {
  const { expandedId, reviewUrl, toggle, registerAnnotation } =
    useExpandedFinding()
  const expanded = expandedId === finding.id
  const style = GROUP_STYLES[finding.group]
  return (
    <div
      ref={(node) => registerAnnotation(finding.id, node)}
      className="px-2 py-1 font-sans"
    >
      <Stack
        bg="panel"
        border="line"
        radius="compact"
        className="overflow-hidden"
      >
        <Inline
          render={
            <button
              type="button"
              onClick={() => toggle(finding)}
              aria-expanded={expanded}
              aria-label={`${expanded ? "Collapse" : "Expand"} finding: ${finding.title}`}
            />
          }
          gap="sm"
          className="min-h-control-sm w-full px-3 py-1 text-left text-label hover:bg-hover"
        >
          <Icon icon={style.glyph} size="sm" className={style.className} />
          <span className={cn("font-medium", style.className)}>
            {style.label}
          </span>
          <span className="min-w-0 flex-1 truncate text-ink">
            {finding.title}
          </span>
          {finding.outdated && <Badgeish>Outdated</Badgeish>}
          {finding.status !== "open" && <Badgeish>{finding.status}</Badgeish>}
          <Icon
            icon={ChevronDown}
            size="sm"
            className={cn(
              "text-ink-subtle transition-transform duration-fast ease-out-quint motion-reduce:transition-none",
              !expanded && "-rotate-90"
            )}
          />
        </Inline>
        {expanded && <FindingDetails finding={finding} reviewUrl={reviewUrl} />}
      </Stack>
    </div>
  )
}

// The expandable body + actions of a finding, shared by the inline diff
// annotation and the side-panel row (non-anchored findings).
function FindingDetails({
  finding,
  reviewUrl,
}: {
  finding: ReviewFinding
  reviewUrl: string
}) {
  const [copied, setCopied] = useState(false)
  const githubUrl =
    finding.github_review_comment_id !== null
      ? `${reviewUrl}#discussion_r${finding.github_review_comment_id}`
      : null

  const copy = () => {
    void navigator.clipboard
      .writeText(findingClipboardText(finding))
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1500)
      })
  }

  return (
    <Stack gap="sm" className="border-t border-line px-3 py-2.5 font-sans">
      <Box className="text-label text-ink-muted">
        <Markdown content={finding.description} />
      </Box>
      {finding.resolution_note && (
        <p className="text-meta text-ink-subtle">
          Resolution: {finding.resolution_note}
        </p>
      )}
      <Inline gap="sm">
        <Button size="compact" variant="outline" onClick={copy}>
          <Icon icon={copied ? Check : Copy} size="sm" />
          {copied ? "Copied" : "Copy"}
        </Button>
        {githubUrl && (
          <a
            href={githubUrl}
            target="_blank"
            rel="noreferrer"
            className={buttonVariants({ variant: "outline", size: "compact" })}
          >
            <Icon icon={GitHub} size="sm" />
            View on GitHub
          </a>
        )}
      </Inline>
    </Stack>
  )
}

function Badgeish({ children }: { children: React.ReactNode }) {
  return (
    <Badge tier="notable" tone="neutral" className="capitalize">
      {children}
    </Badge>
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
        "after:absolute after:inset-y-0 after:left-0 after:w-px after:bg-transparent after:transition-colors after:duration-fast",
        "hover:after:bg-line-strong",
        dragging && "after:bg-line-strong"
      )}
    />
  )
}

function SidePanel({
  detail,
  layout,
  tab,
  onTabChange,
  read,
  expandedId,
  onMarkAllRead,
  onFindingClick,
}: {
  detail: ReviewDetail
  layout: SidePanelLayout
  tab: SideTab
  onTabChange: (tab: SideTab) => void
  read: Set<string>
  expandedId: string | null
  onMarkAllRead: () => void
  onFindingClick: (finding: ReviewFinding) => void
}) {
  const qc = useQueryClient()
  const reReview = useMutation({
    mutationFn: ({ owner, repo, number }: ReviewRef) =>
      api.reReview(owner, repo, number),
    meta: { errorTitle: "Couldn't start re-review" },
    onSuccess: (_result, { owner, repo, number }) => {
      const queryKey = ["review", owner, repo, number]
      qc.setQueryData<ReviewDetail>(queryKey, (old) =>
        old ? { ...old, status: "running" } : old
      )
      void qc.invalidateQueries({ queryKey })
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
      style={layout === "inline" ? { width } : undefined}
      className={cn(
        "flex h-full min-h-0",
        layout === "inline" ? "relative shrink-0" : "w-full"
      )}
    >
      {layout === "inline" && (
        <ReviewPanelResizeHandle width={width} onResize={setWidth} />
      )}
      <aside
        className={cn(
          "flex h-full w-full flex-col overflow-y-auto",
          layout === "inline" && "border-l border-line"
        )}
      >
        <Inline className="h-toolbar shrink-0 border-b border-line px-3">
          <ToggleGroup
            aria-label="Side panel"
            value={[tab]}
            onValueChange={(next: unknown[]) => {
              if (next[0] === "info" || next[0] === "chat") onTabChange(next[0])
            }}
          >
            <ToggleGroupItem value="info">Info</ToggleGroupItem>
            <ToggleGroupItem value="chat">Chat</ToggleGroupItem>
          </ToggleGroup>
        </Inline>

        {tab === "chat" ? (
          <ReviewChat
            owner={detail.owner}
            repo={detail.repo}
            number={detail.number}
            reviewed={detail.status === "idle"}
          />
        ) : (
          <div className="divide-y divide-line">
            <Stack render={<section />} gap="sm" className="px-3 py-3">
              <Inline gap="sm" justify="between" className="text-label">
                <Inline gap="sm">
                  <Badge
                    tier="quiet"
                    dot={detail.status === "running"}
                    tone={
                      detail.status === "running"
                        ? "attention"
                        : detail.status === "error"
                          ? "risk"
                          : detail.status === "none"
                            ? "neutral"
                            : "positive"
                    }
                  >
                    {detail.status === "running"
                      ? "In progress"
                      : detail.status === "error"
                        ? "Failed"
                        : detail.status === "none"
                          ? "Not analyzed"
                          : "Complete"}
                  </Badge>
                  <span className="font-medium text-ink">
                    {detail.status === "running"
                      ? "PR analysis in progress"
                      : detail.status === "error"
                        ? "PR analysis failed"
                        : detail.status === "none"
                          ? "Not analyzed yet"
                          : "PR analysis complete"}
                  </span>
                </Inline>
                <Button
                  size="compact"
                  variant="outline"
                  onClick={() => reReview.mutate(detail)}
                  disabled={reReview.isPending || detail.status === "running"}
                >
                  <Icon icon={RefreshCw} size="sm" />
                  {detail.status === "none" ? "Review" : "Re-review"}
                </Button>
              </Inline>
              <Stack gap="xs" className="text-meta text-ink-subtle">
                <div>
                  {detail.status === "none"
                    ? "Head commit"
                    : "Reviewing commit"}{" "}
                  <span className="font-mono">
                    {detail.head_sha.slice(0, 7) || "—"}
                  </span>
                </div>
                {detail.watch && <div>Watching for new pushes</div>}
                {detail.status === "error" && detail.review_error && (
                  <Inline
                    gap="xs"
                    align="start"
                    className="break-words text-risk"
                  >
                    <Icon icon={AlertTriangle} size="sm" className="mt-0.5" />
                    <span>{detail.review_error}</span>
                  </Inline>
                )}
              </Stack>
            </Stack>

            <FindingSection
              icon={Bug}
              label={`${openBugs.length} Bug${openBugs.length === 1 ? "" : "s"}`}
              emptyLabel={
                detail.status === "none"
                  ? "Not analyzed yet."
                  : "No bugs found."
              }
              findings={bugs}
              read={read}
              expandedId={expandedId}
              reviewUrl={detail.url}
              onFindingClick={onFindingClick}
            />

            <FindingSection
              icon={Flag}
              label={`${openFlags.length} Flag${openFlags.length === 1 ? "" : "s"}`}
              emptyLabel={
                detail.status === "none"
                  ? "Not analyzed yet."
                  : "No issues found."
              }
              findings={flags}
              read={read}
              expandedId={expandedId}
              reviewUrl={detail.url}
              onFindingClick={onFindingClick}
              action={
                detail.findings.length > 0 ? (
                  <Button
                    size="compact"
                    variant="ghost"
                    onClick={onMarkAllRead}
                  >
                    Mark all as read
                  </Button>
                ) : null
              }
            />

            <ChecksSection checks={detail.checks} />
            <PeopleSection
              title="Reviewers"
              people={detail.pr.requested_reviewers}
            />
            <PeopleSection title="Assignees" people={detail.pr.assignees} />
            <Stack render={<section />} gap="sm" className="px-3 py-3">
              <SidePanelHeading>Labels</SidePanelHeading>
              {detail.pr.labels.length === 0 ? (
                <p className="text-meta text-ink-subtle">None</p>
              ) : (
                <Inline gap="xs" wrap>
                  {detail.pr.labels.map((label) => (
                    <Badge key={label.name} tier="notable" tone="neutral">
                      {label.name}
                    </Badge>
                  ))}
                </Inline>
              )}
            </Stack>
          </div>
        )}
      </aside>
    </div>
  )
}

function SidePanelHeading({ children }: { children: React.ReactNode }) {
  return (
    <Box render={<h3 />} className="text-label font-medium text-ink">
      {children}
    </Box>
  )
}

function FindingSection({
  icon,
  label,
  emptyLabel,
  findings,
  read,
  expandedId,
  reviewUrl,
  onFindingClick,
  action,
}: {
  icon: Glyph
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
    <Stack render={<section />} gap="sm" className="px-3 py-3">
      <Inline
        gap="sm"
        justify="between"
        className="min-h-control-sm text-label"
      >
        <Inline
          render={
            <button
              type="button"
              aria-expanded={!collapsed}
              onClick={() => setCollapsed((v) => !v)}
            />
          }
          gap="sm"
          className="font-medium text-ink"
        >
          <Icon icon={icon} size="sm" />
          {label}
          <Icon
            icon={ChevronDown}
            size="sm"
            className={cn(
              "text-ink-subtle transition-transform duration-fast ease-out-quint motion-reduce:transition-none",
              collapsed && "-rotate-90"
            )}
          />
        </Inline>
        {action}
      </Inline>
      {!collapsed &&
        (findings.length === 0 ? (
          <p className="text-meta text-ink-subtle">{emptyLabel}</p>
        ) : (
          <Stack gap="xs">
            {findings.map((finding) => {
              const style = GROUP_STYLES[finding.group]
              const isRead = read.has(finding.id)
              const muted = finding.status !== "open" || isRead
              const anchored = isAnchored(finding)
              const expanded = expandedId === finding.id && !anchored
              return (
                <Box
                  key={finding.id}
                  radius="compact"
                  className={cn(
                    "border border-transparent hover:border-line hover:bg-hover",
                    expanded && "border-line bg-muted",
                    muted && !expanded && "opacity-50"
                  )}
                >
                  <button
                    type="button"
                    onClick={() => onFindingClick(finding)}
                    aria-expanded={anchored ? undefined : expanded}
                    className="block w-full px-2 py-1.5 text-left"
                  >
                    <Inline gap="sm" align="start" className="text-label">
                      <Icon
                        icon={style.glyph}
                        size="sm"
                        className={cn("mt-0.5", style.className)}
                      />
                      <Stack gap="none" className="min-w-0 flex-1">
                        <span className="line-clamp-1 font-medium text-ink">
                          {finding.title || finding.description}
                        </span>
                        <Inline
                          gap="sm"
                          wrap
                          className="text-meta text-ink-subtle"
                        >
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
                        </Inline>
                      </Stack>
                      {!anchored && (
                        <Icon
                          icon={ChevronDown}
                          size="sm"
                          className={cn(
                            "mt-0.5 text-ink-subtle transition-transform duration-fast ease-out-quint motion-reduce:transition-none",
                            !expanded && "-rotate-90"
                          )}
                        />
                      )}
                    </Inline>
                  </button>
                  {expanded && (
                    <FindingDetails finding={finding} reviewUrl={reviewUrl} />
                  )}
                </Box>
              )
            })}
          </Stack>
        ))}
    </Stack>
  )
}

const CHECK_STATE: Record<
  "running" | "passed" | "skipped" | "failed",
  { glyph: Glyph; className: string }
> = {
  running: { glyph: Circle, className: "animate-pulse text-attention" },
  passed: { glyph: CheckCircle, className: "text-positive" },
  skipped: { glyph: Circle, className: "text-ink-subtle" },
  failed: { glyph: XCircle, className: "text-risk" },
}

function checkState(check: ReviewCheckRun): keyof typeof CHECK_STATE {
  if (check.status !== "completed") return "running"
  if (check.conclusion === "success" || check.conclusion === "neutral")
    return "passed"
  if (check.conclusion === "skipped") return "skipped"
  return "failed"
}

function ChecksSection({ checks }: { checks: Array<ReviewCheckRun> }) {
  const failed = checks.filter((check) => checkState(check) === "failed")
  const running = checks.filter((check) => checkState(check) === "running")
  return (
    <Stack render={<section />} gap="sm" className="px-3 py-3">
      <Inline gap="sm">
        <SidePanelHeading>Checks</SidePanelHeading>
        {failed.length > 0 && (
          <Badge tier="quiet" tone="risk">
            {failed.length} failing
          </Badge>
        )}
        {running.length > 0 && (
          <Badge tier="quiet" tone="attention" dot>
            {running.length} running
          </Badge>
        )}
      </Inline>
      {checks.length === 0 ? (
        <p className="text-meta text-ink-subtle">No checks reported.</p>
      ) : (
        <Stack gap="xs" className="max-h-56 overflow-y-auto">
          {checks.map((check, index) => {
            const state = CHECK_STATE[checkState(check)]
            const row = (
              <>
                <Icon
                  icon={state.glyph}
                  size="sm"
                  className={cn(state.className, "motion-reduce:animate-none")}
                />
                <span className="truncate">{check.name}</span>
              </>
            )
            return check.url ? (
              <Inline
                key={`${check.name}-${index}`}
                render={<a href={check.url} target="_blank" rel="noreferrer" />}
                gap="sm"
                className="text-meta text-ink-subtle hover:text-ink"
              >
                {row}
              </Inline>
            ) : (
              <Inline
                key={`${check.name}-${index}`}
                render={<span />}
                gap="sm"
                className="text-meta text-ink-subtle"
              >
                {row}
              </Inline>
            )
          })}
        </Stack>
      )}
    </Stack>
  )
}

function PeopleSection({
  title,
  people,
}: {
  title: string
  people: Array<ReviewUserRef>
}) {
  return (
    <Stack render={<section />} gap="sm" className="px-3 py-3">
      <SidePanelHeading>{title}</SidePanelHeading>
      {people.length === 0 ? (
        <p className="text-meta text-ink-subtle">None</p>
      ) : (
        <Stack gap="xs">
          {people.map((person) => (
            <Inline key={person.login} gap="sm" className="text-meta text-ink">
              <Avatar
                name={person.login}
                src={person.avatar_url ?? undefined}
                size="chat"
                className="size-5"
              />
              {person.login}
            </Inline>
          ))}
        </Stack>
      )}
    </Stack>
  )
}
