import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { CaretDownIcon, CheckIcon, FlagIcon } from "@phosphor-icons/react"
import { MultiFileDiff } from "@pierre/diffs/react"

import type { FileContents } from "@pierre/diffs/react"
import type {
  FileDiff as CoreFileDiff,
  DiffLineAnnotation,
  SelectedLineRange,
} from "@pierre/diffs"
import type {
  PrReviewComment,
  ReviewDiffFile,
  ReviewFinding,
} from "@/features/reviews/lib/api"
import type { DiffStyle } from "@/components/diff/diffUtils"
import type { ResolvedGroup } from "@/features/reviews/lib/diffGroups"
import type { RegisteredDiffInstance } from "@/features/reviews/lib/diffScroll"
import { Markdown } from "@/components/markdown/Markdown"
import {
  DIFF_VIRTUAL_METRICS,
  fileContentsCacheKey,
  useDiffOptions,
} from "@/components/diff/diffUtils"
import {
  CommentComposer,
  InlineComment,
} from "@/features/reviews/components/CommentComposer"
import { InlineFinding } from "@/features/reviews/components/InlineFinding"
import { renderInlineCode } from "@/features/reviews/components/ReviewSidebar"
import { stripLocationLinks } from "@/features/reviews/lib/diffGroups"
import {
  readDiffSelection,
  selectedRangeFromDiff,
} from "@/features/reviews/lib/diffSelection"
import { findingSide } from "@/features/reviews/lib/findings"
import { cn } from "@/lib/utils"

/**
 * Metadata carried by a Pierre diff line annotation. Findings render as the
 * read-only InlineFinding card; a draftComment renders the inline composer; a
 * comment renders an existing PR comment opened from the comments dropdown.
 */
export type ReviewAnnotation =
  | { kind: "finding"; finding: ReviewFinding }
  | { kind: "draftComment"; path: string; range: SelectedLineRange }
  | { kind: "comment"; comment: PrReviewComment }

export type ReviewDiffInstance = RegisteredDiffInstance<ReviewAnnotation>

/**
 * The block header: number + title + stats, then the block description. Pinned
 * at the top of the diff scroller while scrolling the block (Google-Docs feel),
 * stacked above Pierre's in-diff sticky header (z-index 4). A long description
 * scrolls within the pinned header instead of consuming the viewport.
 */
export function GroupHeader({ group }: { group: ResolvedGroup }) {
  const title = useMemo(() => renderInlineCode(group.title), [group.title])
  const summary = useMemo(
    () => (group.summary ? stripLocationLinks(group.summary) : ""),
    [group.summary]
  )
  return (
    <div className="sticky top-0 z-[5] border-b border-border bg-background py-2">
      <div className="flex items-center gap-2">
        <span className="flex size-5 shrink-0 items-center justify-center rounded bg-accent text-[11px] font-medium text-muted-foreground">
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

export const FileDiffCard = memo(function FileDiffCard({
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
  onUpdateOpenComment,
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
    target: ReviewDiffInstance | null
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
      className="scroll-mt-4 overflow-hidden rounded-lg border border-border"
    >
      <div className="flex items-center gap-2 bg-accent px-3 py-2 text-xs">
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
          <div className="bg-card p-4 text-center text-xs text-muted-foreground/70">
            Binary or large file — diff not shown.
          </div>
        ) : (
          <div
            ref={diffWrapperRef}
            onPointerUpCapture={(event) => {
              lastPointerRef.current = { x: event.clientX, y: event.clientY }
            }}
            onMouseUp={handleTextSelection}
            className="overflow-x-auto bg-card font-mono text-[11px] leading-5"
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
