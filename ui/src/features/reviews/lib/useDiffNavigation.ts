/**
 * Where the review page is looking, and how it gets there.
 *
 * Owns the registries of live DOM nodes (file cards, block headers, inline
 * annotation cards, mounted Pierre diff instances), which files are open, and
 * every scroll destination the page offers: a file, a block, a finding's inline
 * card, or an existing PR comment. Targets can be un-mounted when asked for —
 * virtualization windows diff rows in and out — so reveals poll for their node
 * and hold the scroll position until the layout settles.
 */

import { useCallback, useEffect, useRef, useState } from "react"

import type {
  PrReviewComment,
  ReviewDiffFile,
  ReviewFinding,
} from "@/features/reviews/lib/api"
import type { SelectionSide } from "@pierre/diffs"
import type { ResolvedGroup } from "@/features/reviews/lib/diffGroups"
import type {
  DiffVirtualizer,
  RegisteredDiffInstance,
} from "@/features/reviews/lib/diffScroll"
import {
  FINDING_SCROLL_MAX_FRAMES,
  SCROLL_TOP_GAP,
  elementCenterTarget,
  jumpAndHold,
  scrollCardToTop,
  scrollCardToTopVirtual,
  scrollDiffLineToCenter,
  scrollElementToCenter,
} from "@/features/reviews/lib/diffScroll"
import { findingSide, isAnchored } from "@/features/reviews/lib/findings"

export interface DiffNavigationOptions {
  /** Blocks to scroll-spy against, or null when the file tree is in use. */
  groups: Array<ResolvedGroup> | null
  viewed: Set<string>
  setFileViewed: (path: string, viewed: boolean) => void
  filesByPath: Map<string, ReviewDiffFile>
  /** A file just collapsed or was ticked off; anything open inside it should close. */
  onFileHidden: (path: string) => void
}

export interface DiffNavigation<A> {
  selectedFile: string | null
  activeGroup: number | null
  isFileExpanded: (path: string) => boolean
  toggleFileExpanded: (path: string) => void
  toggleFileViewed: (path: string) => void
  registerSection: (path: string, node: HTMLDivElement | null) => void
  registerGroup: (index: number, node: HTMLDivElement | null) => void
  registerAnnotation: (id: string, node: HTMLElement | null) => void
  registerDiffInstance: (
    path: string,
    target: RegisteredDiffInstance<A> | null
  ) => void
  scrollerProbe: (node: HTMLDivElement | null) => void
  virtualizerRef: React.RefObject<DiffVirtualizer | null>
  scrollToFile: (path: string) => void
  scrollToGroup: (index: number) => void
  /** Opens the finding's file and centers its inline card; null only cancels. */
  revealFinding: (finding: ReviewFinding | null) => void
  /** Centers the comment's line, or calls onNoAnchor when it has none. */
  revealComment: (comment: PrReviewComment, onNoAnchor: () => void) => void
}

export function useDiffNavigation<A>({
  groups,
  viewed,
  setFileViewed,
  filesByPath,
  onFileHidden,
}: DiffNavigationOptions): DiffNavigation<A> {
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [expandedFiles, setExpandedFiles] = useState<Record<string, boolean>>(
    {}
  )
  // The block pinned at the top of the diff (scroll-spy), highlighted in the
  // agenda sidebar.
  const [activeGroup, setActiveGroup] = useState<number | null>(null)

  const fileRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const groupRefs = useRef<Record<number, HTMLDivElement | null>>({})
  const annotationRefs = useRef<Record<string, HTMLElement | null>>({})
  const diffInstanceRefs = useRef<
    Record<string, RegisteredDiffInstance<A> | undefined>
  >({})
  const diffScrollElRef = useRef<HTMLDivElement | null>(null)
  const virtualizerRef = useRef<DiffVirtualizer | null>(null)
  // Cancels the in-flight scroll "hold" (see jumpAndHold) when a new navigation
  // begins or the component unmounts, so holds never fight each other.
  const scrollHoldStopRef = useRef<(() => void) | null>(null)
  const revealRequestRef = useRef(0)

  // Latest-value refs so the callbacks below can stay referentially stable
  // (so memo(FileDiffCard) actually skips unrelated re-renders) while still
  // reading current state.
  const viewedRef = useRef(viewed)
  viewedRef.current = viewed
  const expandedRef = useRef(expandedFiles)
  expandedRef.current = expandedFiles
  const filesByPathRef = useRef(filesByPath)
  filesByPathRef.current = filesByPath
  const onFileHiddenRef = useRef(onFileHidden)
  onFileHiddenRef.current = onFileHidden

  const isFileExpanded = useCallback(
    (path: string) => expandedFiles[path] ?? !viewed.has(path),
    [expandedFiles, viewed]
  )

  const toggleFileExpanded = useCallback((path: string) => {
    const current = expandedRef.current[path] ?? !viewedRef.current.has(path)
    const next = !current
    if (!next) onFileHiddenRef.current(path)
    setExpandedFiles((prev) => ({ ...prev, [path]: next }))
  }, [])

  const toggleFileViewed = useCallback(
    (path: string) => {
      const becomingViewed = !viewedRef.current.has(path)
      setFileViewed(path, becomingViewed)
      if (becomingViewed) onFileHiddenRef.current(path)
      setExpandedFiles((prev) => ({ ...prev, [path]: !becomingViewed }))
    },
    [setFileViewed]
  )

  const registerSection = useCallback(
    (path: string, node: HTMLDivElement | null) => {
      fileRefs.current[path] = node
    },
    []
  )
  const registerGroup = useCallback(
    (index: number, node: HTMLDivElement | null) => {
      groupRefs.current[index] = node
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
    (path: string, target: RegisteredDiffInstance<A> | null) => {
      if (target) diffInstanceRefs.current[path] = target
      else delete diffInstanceRefs.current[path]
    },
    []
  )

  // The Virtualizer doesn't forward a ref; grab its scroll element (the
  // grandparent of this hidden probe, which lives in its content div) so
  // scroll-to-file/group can align against it.
  const scrollerProbe = useCallback((node: HTMLDivElement | null) => {
    const scroller = node?.parentElement?.parentElement
    diffScrollElRef.current =
      scroller instanceof HTMLDivElement ? scroller : null
  }, [])

  const scrollCardToTopOf = useCallback(
    (el: HTMLElement | null | undefined) => {
      const scroller = diffScrollElRef.current
      if (!el || !scroller) return
      scrollHoldStopRef.current = virtualizerRef.current
        ? scrollCardToTopVirtual(el, scroller, virtualizerRef.current)
        : scrollCardToTop(el, scroller)
    },
    []
  )

  const scrollToFile = useCallback(
    (path: string) => {
      setSelectedFile(path)
      setExpandedFiles((prev) => ({ ...prev, [path]: true }))
      scrollHoldStopRef.current?.()
      requestAnimationFrame(() => scrollCardToTopOf(fileRefs.current[path]))
    },
    [scrollCardToTopOf]
  )

  const scrollToGroup = useCallback(
    (index: number) => {
      scrollHoldStopRef.current?.()
      requestAnimationFrame(() => scrollCardToTopOf(groupRefs.current[index]))
    },
    [scrollCardToTopOf]
  )

  useEffect(() => () => scrollHoldStopRef.current?.(), [])

  /**
   * Poll for the annotation card at `annotationKey` and center it once it
   * mounts, keeping its diff line centered meanwhile. `onExhausted` fires if
   * the frame budget runs out before the card ever appears.
   */
  const pollForAnnotation = useCallback(
    ({
      requestId,
      annotationKey,
      path,
      line,
      side,
      onExhausted,
    }: {
      requestId: number
      annotationKey: string
      path: string
      line: number
      side: SelectionSide
      onExhausted?: () => void
    }) => {
      let frames = 0
      let lineScrollDone = false
      let mounted = false
      const snap = () => {
        if (requestId !== revealRequestRef.current) return
        const scroller = diffScrollElRef.current
        if (!scroller) return

        // Once the inline card has mounted (its diff rows window in under
        // virtualization), center it and hold as the card settles.
        const annotation = annotationRefs.current[annotationKey]
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

        if (frames++ < FINDING_SCROLL_MAX_FRAMES) requestAnimationFrame(snap)
        else if (!mounted) onExhausted?.()
      }
      requestAnimationFrame(snap)
    },
    []
  )

  const revealFinding = useCallback(
    (finding: ReviewFinding | null) => {
      const requestId = ++revealRequestRef.current
      if (!finding || !isAnchored(finding) || finding.end_line === null) return
      setSelectedFile(finding.file)
      setExpandedFiles((prev) => ({ ...prev, [finding.file]: true }))
      scrollHoldStopRef.current?.()
      pollForAnnotation({
        requestId,
        annotationKey: finding.id,
        path: finding.file,
        line: finding.end_line,
        side: findingSide(finding),
      })
    },
    [pollForAnnotation]
  )

  const revealComment = useCallback(
    (comment: PrReviewComment, onNoAnchor: () => void) => {
      const { path, line } = comment
      const file = filesByPathRef.current.get(path)
      // No inline anchor: the file isn't in the diff, the comment has no line,
      // or it's outdated (its line no longer appears in the current diff).
      if (!file || line === null || comment.is_outdated) {
        onNoAnchor()
        return
      }
      setSelectedFile(path)
      setExpandedFiles((prev) => ({ ...prev, [path]: true }))
      scrollHoldStopRef.current?.()
      pollForAnnotation({
        requestId: ++revealRequestRef.current,
        annotationKey: `comment:${comment.id}`,
        path,
        line,
        side: comment.side === "LEFT" ? "deletions" : "additions",
        // The line never rendered (e.g. collapsed context) — fall back rather
        // than leaving the menu closed with nothing shown.
        onExhausted: onNoAnchor,
      })
    },
    [pollForAnnotation]
  )

  // Scroll-spy: track which block's header is currently pinned at the top of the
  // diff scroller and surface it as the active agenda row (Google-Docs outline).
  useEffect(() => {
    if (!groups || groups.length === 0) {
      setActiveGroup(null)
      return
    }
    const scroller = diffScrollElRef.current
    if (!scroller) return
    let raf = 0
    const compute = () => {
      raf = 0
      const top = scroller.getBoundingClientRect().top
      let current = groups[0]?.index ?? null
      for (const group of groups) {
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
  }, [groups])

  return {
    selectedFile,
    activeGroup,
    isFileExpanded,
    toggleFileExpanded,
    toggleFileViewed,
    registerSection,
    registerGroup,
    registerAnnotation,
    registerDiffInstance,
    scrollerProbe,
    virtualizerRef,
    scrollToFile,
    scrollToGroup,
    revealFinding,
    revealComment,
  }
}
