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
import {
  ArrowClockwiseIcon,
  ArrowSquareOutIcon,
  BugBeetleIcon,
  CaretDownIcon,
  ChatCircleIcon,
  CheckCircleIcon,
  CheckIcon,
  CircleIcon,
  CodeIcon,
  CopyIcon,
  FlagIcon,
  InfoIcon,
  LinkIcon,
  ListBulletsIcon,
  ListChecksIcon,
  ListNumbersIcon,
  QuotesIcon,
  RowsIcon,
  SquareSplitHorizontalIcon,
  TextBIcon,
  TextHIcon,
  TextItalicIcon,
  XCircleIcon,
  XIcon,
} from "@phosphor-icons/react"
import { IoLogoGithub } from "react-icons/io5"
import {
  MultiFileDiff,
  Virtualizer,
  WorkerPoolContextProvider,
  useVirtualizer,
} from "@pierre/diffs/react"
import type { Icon } from "@phosphor-icons/react"
import type { FileContents } from "@pierre/diffs/react"
import type {
  FileDiff as CoreFileDiff,
  DiffLineAnnotation,
  SelectedLineRange,
  SelectionSide,
} from "@pierre/diffs"

import type {
  PrReviewComment,
  ReviewCheckRun,
  ReviewCommentCreate,
  ReviewDetail,
  ReviewDiffFile,
  ReviewFinding,
  ReviewUserRef,
} from "@/lib/api"
import type {
  ReviewSidebarGroup,
  ReviewSidebarView,
} from "@/features/reviews/components/ReviewSidebar"
import type { ChatAttachment } from "@/features/reviews/components/ReviewChat"
import type { DiffStyle } from "@/features/agents/utils/diffUtils"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { PrHeader } from "@/features/reviews/components/PrHeader"
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
import { IconButton } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { api, reviewImageProxyUrl } from "@/lib/api"
import { cn } from "@/lib/utils"

type SideTab = "info" | "chat"

// Metadata carried by a Pierre diff line annotation. Findings render as the
// read-only InlineFinding card; a draftComment renders the inline composer; a
// comment renders an existing PR comment opened from the comments dropdown.
type ReviewAnnotation =
  | { kind: "finding"; finding: ReviewFinding }
  | { kind: "draftComment"; path: string; range: SelectedLineRange }
  | { kind: "comment"; comment: PrReviewComment }

const REVIEW_VIEW_STORAGE_KEY = "open-swe.review.view"
const REVIEW_DIFF_STYLE_STORAGE_KEY = "open-swe.review.diffStyle"
const FINDING_SCROLL_MAX_FRAMES = 120

function readStoredDiffStyle(): DiffStyle {
  if (typeof window === "undefined") return "unified"
  return window.localStorage.getItem(REVIEW_DIFF_STYLE_STORAGE_KEY) === "split"
    ? "split"
    : "unified"
}

// One attachment for a single-side line range. Deletions resolve against the
// original file, additions against the modified file.
function makeSideAttachment(
  file: ReviewDiffFile,
  side: "deletions" | "additions",
  fromLine: number,
  toLine: number
): ChatAttachment {
  const source =
    side === "deletions" ? file.originalContent : file.modifiedContent
  const lines = source.split("\n")
  const start = Math.max(1, Math.min(fromLine, toLine))
  const end = Math.max(fromLine, toLine)
  const snippet = lines.slice(start - 1, end).join("\n")
  const sideLabel = side === "deletions" ? "L" : "R"
  const lineLabel =
    start === end ? `${sideLabel}${start}` : `${sideLabel}${start}-${end}`
  const language = file.path.includes(".")
    ? (file.path.split(".").pop() ?? "")
    : ""
  return {
    id: crypto.randomUUID(),
    path: file.path,
    lineLabel,
    language,
    snippet,
  }
}

// Build chat attachments from the selected range. A range can span from a
// deletion to an addition (side !== endSide) when dragging across a replaced
// block; slicing one file by start..end would paste the wrong lines, so each
// side is collected separately.
function buildSelectionAttachments(
  file: ReviewDiffFile,
  range: SelectedLineRange
): Array<ChatAttachment> {
  const startSide = range.side ?? "additions"
  const endSide = range.endSide ?? startSide
  if (startSide === endSide) {
    return [makeSideAttachment(file, startSide, range.start, range.end)]
  }
  const deletionLine = startSide === "deletions" ? range.start : range.end
  const additionLine = startSide === "additions" ? range.start : range.end
  return [
    makeSideAttachment(file, "deletions", deletionLine, deletionLine),
    makeSideAttachment(file, "additions", additionLine, additionLine),
  ]
}

interface ShadowRootWithSelection {
  getSelection?: () => Selection | null
}

// Read the active selection inside a <diffs-container>'s open shadow root.
// Chromium exposes ShadowRoot.getSelection(); elsewhere fall back to the document
// selection (events from open shadow DOM are composed/retargeted).
function readDiffSelection(
  container: Element | null | undefined
): Selection | null {
  const root = container?.shadowRoot
  if (root) {
    const scoped = (root as ShadowRoot & ShadowRootWithSelection).getSelection
    if (typeof scoped === "function") return scoped.call(root)
  }
  return typeof document !== "undefined" ? document.getSelection() : null
}

// Map a selection boundary node to its file line number + side via the
// data-line / data-line-type attributes Pierre stamps on every line div.
function lineMetaFromNode(
  node: Node | null
): { line: number; side: SelectionSide } | null {
  const el = node instanceof Element ? node : (node?.parentElement ?? null)
  const lineEl = el?.closest("[data-line]")
  if (!lineEl) return null
  const line = Number(lineEl.getAttribute("data-line"))
  if (!Number.isInteger(line)) return null
  const type = lineEl.getAttribute("data-line-type") ?? ""
  return { line, side: type.includes("deletion") ? "deletions" : "additions" }
}

// Resolve the current native text selection inside a diff to a line range, so a
// plain text highlight can drive "Add to Chat" (Devin-style) instead of a
// gutter drag.
function selectedRangeFromDiff(
  container: Element | null | undefined
): SelectedLineRange | null {
  const selection = readDiffSelection(container)
  if (!selection || selection.isCollapsed || selection.rangeCount === 0)
    return null
  const range = selection.getRangeAt(0)
  const start = lineMetaFromNode(range.startContainer)
  const end = lineMetaFromNode(range.endContainer)
  if (!start || !end) return null
  return {
    start: start.line,
    side: start.side,
    end: end.line,
    endSide: end.side,
  }
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

function scrollFindingLineToCenter({
  target,
  finding,
  scroller,
}: {
  target: RegisteredDiffInstance
  finding: ReviewFinding
  scroller: HTMLElement
}): boolean {
  if (finding.end_line === null) return false
  return scrollDiffLineToCenter(
    target,
    finding.end_line,
    findingSide(finding),
    scroller
  )
}

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

function selectionSideToGithub(
  side: SelectionSide | undefined
): "LEFT" | "RIGHT" {
  return side === "deletions" ? "LEFT" : "RIGHT"
}

// Map a Pierre selection range to a GitHub inline-comment payload. GitHub
// forbids multi-line ranges that span sides, so a cross-side selection collapses
// to a single line on the end side; same-side ranges keep their start_line.
function buildCommentPayload(
  path: string,
  range: SelectedLineRange,
  body: string
): ReviewCommentCreate {
  const startSide = range.side ?? "additions"
  const endSide = range.endSide ?? startSide
  if (startSide !== endSide) {
    return {
      path,
      line: range.end,
      side: selectionSideToGithub(endSide),
      body,
      start_line: null,
      start_side: null,
    }
  }
  const side = selectionSideToGithub(endSide)
  const lo = Math.min(range.start, range.end)
  const hi = Math.max(range.start, range.end)
  return {
    path,
    line: hi,
    side,
    body,
    start_line: lo < hi ? lo : null,
    start_side: lo < hi ? side : null,
  }
}

function commentRangeLabel(range: SelectedLineRange): string {
  const side = (range.endSide ?? range.side) === "deletions" ? "L" : "R"
  const lo = Math.min(range.start, range.end)
  const hi = Math.max(range.start, range.end)
  return lo === hi ? `${side}${hi}` : `${side}${lo}-${hi}`
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
  onCloseOpenComment?: () => void
}

export function ReviewMainBody({
  detail,
  diffFiles,
  variant = "full",
  onExpand,
  openComment,
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
  onCloseOpenComment,
}: {
  detail: ReviewDetail
  diffFiles: Array<ReviewDiffFile> | null
  variant: ReviewMainBodyVariant
  onExpand?: () => void
  openComment?: PrReviewComment | null
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
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const fileRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const diffInstanceRefs = useRef<
    Record<string, RegisteredDiffInstance | undefined>
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
  expandedFindingRef.current = expandedFinding

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
  viewedRef.current = viewed
  const expandedRef = useRef(expandedFiles)
  expandedRef.current = expandedFiles

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

  useEffect(() => () => scrollHoldStopRef.current?.(), [])

  // Scroll-spy: track which block's header is currently pinned at the top of the
  // diff scroller and surface it as the active agenda row (Google-Docs outline).
  useEffect(() => {
    if (view !== "ai" || !groupedView || groupedView.length === 0) {
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
  filesByPathRef.current = filesByPath

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
    (path: string, target: RegisteredDiffInstance | null) => {
      if (target) diffInstanceRefs.current[path] = target
      else delete diffInstanceRefs.current[path]
    },
    []
  )

  const toggleExpanded = useCallback((path: string) => {
    const current = expandedRef.current[path] ?? !viewedRef.current.has(path)
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
    (path: string, range: SelectedLineRange) => {
      const file = filesByPathRef.current.get(path)
      if (!file) return
      for (const attachment of buildSelectionAttachments(file, range)) {
        composer?.addAttachment(attachment)
      }
      setSideTab("chat")
      setUserSelection(null)
    },
    [composer]
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
  userSelectionRef.current = userSelection
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

        const diffTarget = diffInstanceRefs.current[finding.file]
        if (diffTarget) {
          lineScrollDone = scrollFindingLineToCenter({
            target: diffTarget,
            finding,
            scroller,
          })
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
  closeOpenCommentRef.current = onCloseOpenComment
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
      const diffTarget = diffInstanceRefs.current[path]
      if (diffTarget) {
        lineScrollDone = scrollDiffLineToCenter(
          diffTarget,
          line,
          side,
          scroller
        )
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

  const renderFileCard = (file: ReviewDiffFile) => {
    // Keep the range highlighted while its comment composer is open, so the
    // user can see exactly which lines they're commenting on.
    const selectedLines =
      expandedFinding?.file === file.path && isAnchored(expandedFinding)
        ? findingSelectedRange(expandedFinding)
        : commentDraft?.file === file.path
          ? commentDraft.range
          : userSelection?.file === file.path
            ? userSelection.range
            : null
    return (
      <FileDiffCard
        key={file.path}
        file={file}
        findings={findingsByFile.get(file.path) ?? NO_FINDINGS}
        selectedLines={selectedLines}
        viewed={viewed.has(file.path)}
        onToggleViewed={toggleViewed}
        expanded={expandedFiles[file.path] ?? !viewed.has(file.path)}
        onToggleExpanded={toggleExpanded}
        onSelectLines={selectLines}
        onAddToChat={embedded ? undefined : addToChat}
        registerSection={registerSection}
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

  return (
    <ExpandedFindingContext.Provider value={expandedFindingCtx}>
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <main className="relative flex min-h-0 min-w-0 flex-1">
          {!embedded && (
            <div className="hidden w-72 shrink-0 flex-col border-r border-border bg-[var(--ui-sidebar)] lg:flex">
              <ReviewSidebarPanel data={sidebarData} />
            </div>
          )}
          <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
            {embedded && (
              <div className="flex h-9 shrink-0 items-center justify-end border-b border-border px-3">
                <button
                  type="button"
                  onClick={onExpand}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
                >
                  <ArrowSquareOutIcon className="size-3" />
                  Open full review
                </button>
              </div>
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
                  state={detail.pr.state}
                  headRef={detail.pr.head_ref}
                  baseRef={detail.pr.base_ref}
                  author={detail.pr.author?.login}
                  stats={{
                    changedFiles: detail.pr.changed_files,
                    additions: detail.pr.additions,
                    deletions: detail.pr.deletions,
                  }}
                />
                <div
                  className={cn(
                    "mt-4 rounded-lg border border-border p-4",
                    embedded ? "bg-[var(--ui-surface)]" : "bg-card"
                  )}
                >
                  {detail.pr.body ? (
                    <Markdown
                      content={detail.pr.body}
                      transformImageUrl={transformPrImage}
                    />
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      This PR has no description.
                    </p>
                  )}
                </div>

                <div className="mt-6">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <h2 className="text-sm font-medium">Changes</h2>
                    <div className="flex items-center gap-3">
                      {linesLeft !== null && (
                        <span className="text-xs text-muted-foreground">
                          {linesLeft === 0
                            ? "All lines reviewed"
                            : `${linesLeft} lines left`}
                        </span>
                      )}
                      {diffFiles && diffFiles.length > 0 && (
                        <DiffStyleToggle
                          value={diffStyle}
                          onChange={setDiffStyle}
                        />
                      )}
                    </div>
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
                    <div className="space-y-3">
                      {diffFiles.map(renderFileCard)}
                    </div>
                  )}
                </div>
              </Virtualizer>
            </WorkerPoolContextProvider>
          </div>
        </main>

        {!embedded && (
          <SidePanel
            detail={detail}
            tab={sideTab}
            onTabChange={setSideTab}
            read={read}
            expandedId={expandedId}
            onMarkAllRead={markAllRead}
            onFindingClick={openFromPanel}
          />
        )}
      </div>
    </ExpandedFindingContext.Provider>
  )
}

function DiffStyleToggle({
  value,
  onChange,
}: {
  value: DiffStyle
  onChange: (value: DiffStyle) => void
}) {
  return (
    <div className="flex items-center gap-0.5 rounded-md border border-border p-0.5">
      <DiffStyleButton
        active={value === "unified"}
        label="Unified view"
        onClick={() => onChange("unified")}
      >
        <RowsIcon className="size-3.5" />
      </DiffStyleButton>
      <DiffStyleButton
        active={value === "split"}
        label="Split view"
        onClick={() => onChange("split")}
      >
        <SquareSplitHorizontalIcon className="size-3.5" />
      </DiffStyleButton>
    </div>
  )
}

function DiffStyleButton({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean
  label: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={cn(
        "flex size-5 items-center justify-center rounded text-muted-foreground transition-colors",
        active ? "bg-muted text-foreground" : "hover:text-foreground"
      )}
    >
      {children}
    </button>
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

// The block header: number + title + stats, then the block description. Pinned
// at the top of the diff scroller while scrolling the block (Google-Docs feel),
// stacked above Pierre's in-diff sticky header (z-index 4). A long description
// scrolls within the pinned header instead of consuming the viewport.
function GroupHeader({ group }: { group: ResolvedGroup }) {
  const title = useMemo(() => renderInlineCode(group.title), [group.title])
  const summary = useMemo(
    () => (group.summary ? stripLocationLinks(group.summary) : ""),
    [group.summary]
  )
  return (
    <div className="sticky top-0 z-[5] border-b border-border bg-background py-2">
      <div className="flex items-center gap-2">
        <span className="flex size-5 shrink-0 items-center justify-center rounded bg-[var(--ui-panel-2)] text-[11px] font-medium text-muted-foreground">
          {group.index}
        </span>
        <h3 className="min-w-0 flex-1 truncate text-sm font-medium">{title}</h3>
        <span className="flex shrink-0 items-center gap-1.5 font-mono text-[11px]">
          {group.additions > 0 && (
            <span className="text-emerald-500">+{group.additions}</span>
          )}
          {group.deletions > 0 && (
            <span className="text-red-500">-{group.deletions}</span>
          )}
        </span>
      </div>
      {summary && (
        <div className="mt-2 max-h-40 overflow-y-auto text-xs text-muted-foreground">
          <Markdown content={summary} />
        </div>
      )}
    </div>
  )
}

const FileDiffCard = memo(function FileDiffCard({
  file,
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
  onCloseOpenComment,
}: {
  file: ReviewDiffFile
  findings: Array<ReviewFinding>
  selectedLines: SelectedLineRange | null
  viewed: boolean
  onToggleViewed: (path: string) => void
  expanded: boolean
  onToggleExpanded: (path: string) => void
  onSelectLines: (path: string, range: SelectedLineRange | null) => void
  onAddToChat?: (path: string, range: SelectedLineRange) => void
  registerSection: (path: string, node: HTMLDivElement | null) => void
  registerDiffInstance: (
    path: string,
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
  onCloseOpenComment?: () => void
}) {
  // No chat means no line-selection → "Add to Chat" affordance (embedded view).
  const selectable = Boolean(onAddToChat)
  // Commenting rides the same gutter "+" as selection, so it's available only
  // where the gutter utility is enabled (the full reviews page).
  const commentable = selectable && Boolean(onStartComment)
  const diffOptions = useDiffOptions(diffStyle)
  const diffWrapperRef = useRef<HTMLDivElement | null>(null)
  const lastPointerRef = useRef<{ x: number; y: number } | null>(null)
  const [popup, setPopup] = useState<{
    range: SelectedLineRange
    x: number
    y: number
  } | null>(null)

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
    return extra.length > 0
      ? [...findingAnnotations, ...extra]
      : findingAnnotations
  }, [findingAnnotations, commentDraftRange, openComment, file.path])

  // The gutter "+" drives comments: a click comments on one line, and a drag down
  // the gutter comments across a range (Pierre's gutter selection, which needs
  // enableLineSelection). "Add to Chat" instead comes from a native text highlight
  // on the code (handleTextSelection) — Pierre leaves code content user-selectable
  // and only line-selects from the gutter, so the two don't collide. onLineSelectionEnd
  // bails if a native text selection is present, so a code highlight never opens the
  // composer (belt-and-suspenders in case Pierre ever reports a content drag).
  const cardOptions = useMemo(
    () => ({
      ...diffOptions,
      enableLineSelection: commentable,
      enableGutterUtility: commentable,
      onGutterUtilityClick: commentable
        ? (range: SelectedLineRange) => onStartComment?.(file.path, range)
        : undefined,
      onLineSelectionChange: commentable
        ? (range: SelectedLineRange | null) => onSelectLines(file.path, range)
        : undefined,
      onLineSelectionEnd: commentable
        ? (range: SelectedLineRange | null) => {
            if (!range) return
            const host =
              diffWrapperRef.current?.querySelector("diffs-container")
            const native = readDiffSelection(host)
            if (native && !native.isCollapsed && native.rangeCount > 0) return
            onStartComment?.(file.path, range)
          }
        : undefined,
      onPostRender: (
        node: HTMLElement,
        instance: CoreFileDiff<ReviewAnnotation>
      ) => registerDiffInstance(file.path, { host: node, instance }),
    }),
    [
      diffOptions,
      commentable,
      onStartComment,
      onSelectLines,
      file.path,
      registerDiffInstance,
    ]
  )

  // On mouse release, turn any native text highlight inside the diff into a line
  // range: highlight rows (controlled selection) + show the "Add to Chat" popup
  // at the cursor. A collapsed selection (plain click) is ignored.
  const handleTextSelection = useCallback(() => {
    if (!selectable) return
    const container = diffWrapperRef.current?.querySelector("diffs-container")
    const range = selectedRangeFromDiff(container)
    if (!range) return
    onSelectLines(file.path, range)
    const pointer = lastPointerRef.current
    if (pointer) setPopup({ range, x: pointer.x, y: pointer.y })
  }, [selectable, file.path, onSelectLines])

  const addPopupToChat = useCallback(() => {
    if (popup) onAddToChat?.(file.path, popup.range)
    setPopup(null)
    // Clear the lingering native highlight once added.
    readDiffSelection(
      diffWrapperRef.current?.querySelector("diffs-container")
    )?.removeAllRanges()
  }, [popup, onAddToChat, file.path])

  // Drop the popup once the selection clears (e.g. added via ⌘L, or a finding
  // took focus) so it can't add the same range twice.
  useEffect(() => {
    if (!selectedLines) setPopup(null)
  }, [selectedLines])

  // Opening a comment draft owns the "+"; never show "Add to Chat" alongside it
  // (a single "+" click can otherwise both open the composer and arm the popup).
  useEffect(() => {
    if (commentDraftRange) setPopup(null)
  }, [commentDraftRange])

  const oldFile = useMemo<FileContents>(
    () => ({
      name: file.path,
      contents: file.originalContent,
      cacheKey: fileContentsCacheKey(file.path, "old", file.originalContent),
    }),
    [file.path, file.originalContent]
  )
  const newFile = useMemo<FileContents>(
    () => ({
      name: file.path,
      contents: file.modifiedContent,
      cacheKey: fileContentsCacheKey(file.path, "new", file.modifiedContent),
    }),
    [file.path, file.modifiedContent]
  )

  const sectionRef = useCallback(
    (node: HTMLDivElement | null) => registerSection(file.path, node),
    [registerSection, file.path]
  )
  useEffect(
    () => () => registerDiffInstance(file.path, null),
    [file.path, registerDiffInstance]
  )
  const renderAnnotation = useCallback(
    (annotation: DiffLineAnnotation<ReviewAnnotation>) => {
      const meta = annotation.metadata
      if (meta.kind === "finding")
        return <InlineFinding finding={meta.finding} />
      if (meta.kind === "comment")
        return (
          <InlineComment
            comment={meta.comment}
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
    [owner, repo, prNumber, onCloseComment, onCloseOpenComment]
  )

  return (
    <div
      ref={sectionRef}
      className="scroll-mt-4 overflow-hidden rounded-lg border border-[var(--ui-border)]"
    >
      <div className="flex items-center gap-2 bg-[var(--ui-panel-2)] px-3 py-2 text-xs">
        <button
          type="button"
          onClick={() => onToggleExpanded(file.path)}
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
            onClick={() => onToggleViewed(file.path)}
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
          <div
            ref={diffWrapperRef}
            onPointerUpCapture={(event) => {
              lastPointerRef.current = { x: event.clientX, y: event.clientY }
            }}
            onMouseUp={handleTextSelection}
            className="overflow-x-auto bg-[var(--ui-panel)] font-mono text-[11px] leading-5"
          >
            <MultiFileDiff<ReviewAnnotation>
              oldFile={oldFile}
              newFile={newFile}
              options={cardOptions}
              metrics={DIFF_VIRTUAL_METRICS}
              lineAnnotations={lineAnnotations}
              selectedLines={selectedLines}
              renderAnnotation={renderAnnotation}
            />
            {popup && !commentDraftRange && (
              <AddToChatPopup
                x={popup.x}
                y={popup.y}
                onAdd={addPopupToChat}
                onDismiss={() => setPopup(null)}
              />
            )}
          </div>
        ))}
    </div>
  )
})

function AddToChatPopup({
  x,
  y,
  onAdd,
  onDismiss,
}: {
  x: number
  y: number
  onAdd: () => void
  onDismiss: () => void
}) {
  // Positioned fixed at the pointer-release point so it escapes the diff's
  // overflow clipping. Dismiss on Escape, scroll, or any outside pointer-down.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onDismiss()
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Element && target.closest("[data-add-to-chat]"))
        return
      onDismiss()
    }
    window.addEventListener("keydown", onKeyDown)
    window.addEventListener("pointerdown", onPointerDown)
    // Capture so it also catches scrolls from the diff scroll container.
    window.addEventListener("scroll", onDismiss, true)
    return () => {
      window.removeEventListener("keydown", onKeyDown)
      window.removeEventListener("pointerdown", onPointerDown)
      window.removeEventListener("scroll", onDismiss, true)
    }
  }, [onDismiss])

  return (
    <div
      data-add-to-chat
      style={{ position: "fixed", top: y, left: x }}
      className="z-50 -translate-y-[calc(100%+4px)] font-sans"
    >
      <button
        type="button"
        onClick={onAdd}
        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-popover px-2 py-1 text-[11px] font-medium text-popover-foreground shadow-md hover:bg-muted"
      >
        Add to Chat
        <kbd className="rounded border border-border px-1 text-[10px] text-muted-foreground">
          ⌘L
        </kbd>
      </button>
    </div>
  )
}

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
  Icon: Icon
}

// Grouped to match GitHub's comment toolbar (format group, then list group).
const MARKDOWN_TOOLBAR: ReadonlyArray<ReadonlyArray<ToolbarItem>> = [
  [
    { action: "heading", label: "Heading", Icon: TextHIcon },
    { action: "bold", label: "Bold", Icon: TextBIcon },
    { action: "italic", label: "Italic", Icon: TextItalicIcon },
    { action: "quote", label: "Quote", Icon: QuotesIcon },
    { action: "code", label: "Code", Icon: CodeIcon },
    { action: "link", label: "Link", Icon: LinkIcon },
  ],
  [
    { action: "ul", label: "Bulleted list", Icon: ListBulletsIcon },
    { action: "ol", label: "Numbered list", Icon: ListNumbersIcon },
    { action: "task", label: "Task list", Icon: ListChecksIcon },
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
  useEffect(() => {
    textareaRef.current?.focus()
  }, [])
  const mutation = useMutation({
    mutationFn: (body: string) =>
      api.createReviewComment(
        owner,
        repo,
        prNumber,
        buildCommentPayload(path, range, body)
      ),
  })
  const submit = () => {
    const body = value.trim()
    if (!body || mutation.isPending) return
    mutation.mutate(body)
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
  const posted = mutation.data
  const tabClass = (active: boolean) =>
    cn(
      "rounded px-2 py-0.5 text-[11px]",
      active
        ? "bg-[var(--ui-panel-2)] font-medium text-foreground"
        : "text-muted-foreground hover:text-foreground"
    )
  return (
    <div className="px-2 py-1 font-sans">
      <div className="overflow-hidden rounded-md border border-[var(--ui-border)] bg-[var(--ui-surface)]">
        <div className="flex items-center gap-1.5 border-b border-[var(--ui-border)] px-2 py-1 text-[11px]">
          <ChatCircleIcon className="size-3 text-muted-foreground" />
          <span className="font-medium">
            Add a comment on line {commentRangeLabel(range)}
          </span>
          <IconButton
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="Close comment"
            className="ml-auto"
            onClick={onClose}
          >
            <XIcon />
          </IconButton>
        </div>
        {posted ? (
          <div className="flex items-center gap-2 px-3 py-2.5 text-[11px] text-muted-foreground">
            <CheckCircleIcon className="size-3.5 text-emerald-500" />
            Comment posted
            <a
              href={posted.html_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-foreground hover:underline"
            >
              <IoLogoGithub className="size-3" />
              View on GitHub
            </a>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-1 border-b border-[var(--ui-border)] px-1.5 py-1">
              <button
                type="button"
                onClick={() => setMode("write")}
                aria-selected={mode === "write"}
                className={tabClass(mode === "write")}
              >
                Write
              </button>
              <button
                type="button"
                onClick={() => setMode("preview")}
                aria-selected={mode === "preview"}
                className={tabClass(mode === "preview")}
              >
                Preview
              </button>
              {mode === "write" && (
                <div className="ml-auto flex items-center gap-0.5">
                  {MARKDOWN_TOOLBAR.map((group, groupIndex) => (
                    <Fragment key={group[0]?.action ?? groupIndex}>
                      {groupIndex > 0 && (
                        <span className="mx-0.5 h-4 w-px bg-[var(--ui-border)]" />
                      )}
                      {group.map(({ action, label, Icon }) => (
                        <IconButton
                          key={action}
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          aria-label={label}
                          title={label}
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => applyAction(action)}
                        >
                          <Icon className="size-4" />
                        </IconButton>
                      ))}
                    </Fragment>
                  ))}
                </div>
              )}
            </div>
            <div className="p-2">
              {mode === "write" ? (
                <Textarea
                  ref={textareaRef}
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      (event.metaKey || event.ctrlKey) &&
                      event.key === "Enter"
                    ) {
                      event.preventDefault()
                      submit()
                    } else if (event.key === "Escape") {
                      event.preventDefault()
                      onClose()
                    }
                  }}
                  placeholder="Leave a comment…"
                  rows={3}
                  className="resize-y text-xs"
                />
              ) : (
                <div className="min-h-16 rounded-md border border-input bg-input/20 px-2 py-2 text-xs">
                  {value.trim() ? (
                    <Markdown content={value} />
                  ) : (
                    <span className="text-muted-foreground">
                      Nothing to preview
                    </span>
                  )}
                </div>
              )}
              {mutation.isError && (
                <p className="mt-1.5 text-[11px] text-destructive">
                  {mutation.error instanceof Error
                    ? mutation.error.message
                    : "Failed to post comment"}
                </p>
              )}
              <div className="mt-2 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={submit}
                  disabled={!value.trim() || mutation.isPending}
                  className="rounded bg-foreground px-2 py-1 text-[11px] font-medium text-background disabled:opacity-50"
                >
                  {mutation.isPending ? "Posting…" : "Comment"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// An existing PR comment opened from the comments dropdown, rendered inline at
// its line through the same annotation portal as InlineFinding. Read-only; the
// node is registered so the dropdown can scroll it into view. Links to the
// full thread on GitHub.
function InlineComment({
  comment,
  onClose,
}: {
  comment: PrReviewComment
  onClose: () => void
}) {
  const { registerAnnotation } = useExpandedFinding()
  const sideLabel = comment.side === "LEFT" ? "L" : "R"
  return (
    <div
      ref={(node) => registerAnnotation(`comment:${comment.id}`, node)}
      className="px-2 py-1 font-sans"
    >
      <div className="overflow-hidden rounded-md border border-[var(--ui-border)] bg-[var(--ui-surface)]">
        <div className="flex items-center gap-1.5 border-b border-[var(--ui-border)] px-2 py-1 text-[11px]">
          {comment.author_avatar_url ? (
            <img
              src={comment.author_avatar_url}
              alt=""
              className="size-4 shrink-0 rounded-full"
            />
          ) : (
            <span className="size-4 shrink-0 rounded-full bg-muted" />
          )}
          <span className="font-medium">{comment.author}</span>
          {comment.line !== null && (
            <span className="font-mono text-muted-foreground">
              {sideLabel}
              {comment.line}
            </span>
          )}
          <div className="ml-auto flex items-center gap-0.5">
            <a
              href={comment.html_url}
              target="_blank"
              rel="noreferrer"
              aria-label="View on GitHub"
              title="View on GitHub"
              className="inline-flex size-5 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground"
            >
              <IoLogoGithub className="size-3" />
            </a>
            <IconButton
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label="Close comment"
              onClick={onClose}
            >
              <XIcon />
            </IconButton>
          </div>
        </div>
        <div className="px-3 py-2.5 text-xs text-muted-foreground">
          <Markdown content={comment.body} />
        </div>
      </div>
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
  const Icon = style.Icon
  return (
    <div
      ref={(node) => registerAnnotation(finding.id, node)}
      className="px-2 py-1 font-sans"
    >
      <div className="overflow-hidden rounded-md border border-[var(--ui-border)] bg-[var(--ui-surface)]">
        <button
          type="button"
          onClick={() => toggle(finding)}
          aria-expanded={expanded}
          aria-label={`${expanded ? "Collapse" : "Expand"} finding: ${finding.title}`}
          className="flex w-full items-center gap-1.5 px-2 py-1 text-left text-[11px]"
        >
          <Icon className={cn("size-3 shrink-0", style.className)} />
          <span className={cn("font-medium", style.className)}>
            {style.label}
          </span>
          <span className="min-w-0 flex-1 truncate text-foreground">
            {finding.title}
          </span>
          {finding.outdated && <Badgeish>Outdated</Badgeish>}
          {finding.status !== "open" && <Badgeish>{finding.status}</Badgeish>}
          <CaretDownIcon
            className={cn(
              "size-3 shrink-0 text-muted-foreground transition-transform",
              !expanded && "-rotate-90"
            )}
          />
        </button>
        {expanded && <FindingDetails finding={finding} reviewUrl={reviewUrl} />}
      </div>
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
    <div className="border-t border-[var(--ui-border)] px-3 py-2.5 font-sans">
      <div className="text-xs text-muted-foreground">
        <Markdown content={finding.description} />
      </div>
      {finding.resolution_note && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          Resolution: {finding.resolution_note}
        </p>
      )}
      <div className="mt-2.5 flex items-center gap-2">
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
  icon: (typeof GROUP_STYLES)["bug"]["Icon"]
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
