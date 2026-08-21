/**
 * Everything the review page remembers in localStorage: how the diff is laid
 * out, which files the reader has ticked off, which findings they have read,
 * and how wide the side panel is.
 *
 * Reads are defensive — a missing, malformed, or unavailable store (private
 * mode, SSR) falls back to the default rather than throwing.
 */

import { useCallback, useEffect, useRef, useState } from "react"

import type { DiffStyle } from "@/components/diff/diffUtils"

export type ReviewView = "ai" | "files"

const DIFF_STYLE_KEY = "open-swe.review.diffStyle"
const VIEW_KEY = "open-swe.review.view"
const PANEL_WIDTH_KEY = "open-swe.review-panel.width"

export const REVIEW_PANEL_DEFAULT_WIDTH = 420
export const REVIEW_PANEL_MIN_WIDTH = 360
// Keep at least this much room for the PR content column so the panel can grow
// wide without squeezing the diff/description below a usable width.
const REVIEW_PANEL_MIN_MAIN_WIDTH = 480

function readItem(key: string): string | null {
  if (typeof window === "undefined") return null
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeItem(key: string, value: string): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* ignore persistence failures (private mode, quota, SSR) */
  }
}

function readIdSet(key: string): Set<string> {
  const raw = readItem(key)
  if (!raw) return new Set()
  try {
    const parsed: unknown = JSON.parse(raw)
    return new Set(
      Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : []
    )
  } catch {
    return new Set()
  }
}

function writeIdSet(key: string, ids: Set<string>): void {
  writeItem(key, JSON.stringify([...ids]))
}

export function readDiffStyle(): DiffStyle {
  return readItem(DIFF_STYLE_KEY) === "split" ? "split" : "unified"
}

/** Unified vs split diff, remembered across PRs. */
export function useDiffStylePref(): [DiffStyle, (next: DiffStyle) => void] {
  const [diffStyle, setState] = useState<DiffStyle>(readDiffStyle)
  const setDiffStyle = useCallback((next: DiffStyle) => {
    setState(next)
    writeItem(DIFF_STYLE_KEY, next)
  }, [])
  return [diffStyle, setDiffStyle]
}

export function readReviewView(): ReviewView | null {
  const stored = readItem(VIEW_KEY)
  return stored === "ai" || stored === "files" ? stored : null
}

/**
 * The sidebar view the user explicitly picked, or null while they haven't —
 * the caller then follows whether fresh AI groups exist.
 */
export function useReviewViewPref(): [
  ReviewView | null,
  (next: ReviewView) => void,
] {
  const [view, setState] = useState<ReviewView | null>(readReviewView)
  const setView = useCallback((next: ReviewView) => {
    setState(next)
    writeItem(VIEW_KEY, next)
  }, [])
  return [view, setView]
}

export function viewedFilesKey(
  owner: string,
  repo: string,
  number: number,
  headSha: string
): string {
  return `open-swe.review.viewed.${owner}/${repo}/${number}.${headSha}`
}

/** Files ticked off as reviewed, scoped to one PR at one head commit. */
export function useViewedFiles(
  owner: string,
  repo: string,
  number: number,
  headSha: string
) {
  const key = viewedFilesKey(owner, repo, number, headSha)
  const [viewed, setViewed] = useState<Set<string>>(() => readIdSet(key))

  const setFileViewed = useCallback(
    (path: string, isViewed: boolean) => {
      setViewed((prev) => {
        const next = new Set(prev)
        if (isViewed) next.add(path)
        else next.delete(path)
        writeIdSet(key, next)
        return next
      })
    },
    [key]
  )

  return { viewed, setFileViewed }
}

export function readFindingsKey(threadId: string): string {
  return `open-swe.review.read.${threadId}`
}

/** Findings the reader has opened, scoped to one review thread. */
export function useReadFindings(threadId: string) {
  const key = readFindingsKey(threadId)
  const [read, setRead] = useState<Set<string>>(() => readIdSet(key))

  const markRead = useCallback(
    (id: string) => {
      setRead((prev) => {
        if (prev.has(id)) return prev
        const next = new Set(prev).add(id)
        writeIdSet(key, next)
        return next
      })
    },
    [key]
  )

  const markAllRead = useCallback(
    (ids: Array<string>) => {
      const next = new Set(ids)
      setRead(next)
      writeIdSet(key, next)
    },
    [key]
  )

  return { read, markRead, markAllRead }
}

export function reviewPanelMaxWidth(availableWidth: number): number {
  return Math.max(
    REVIEW_PANEL_MIN_WIDTH,
    availableWidth - REVIEW_PANEL_MIN_MAIN_WIDTH
  )
}

export function clampReviewPanelWidth(
  width: number,
  availableWidth: number
): number {
  return Math.min(
    reviewPanelMaxWidth(availableWidth),
    Math.max(REVIEW_PANEL_MIN_WIDTH, width)
  )
}

export function readReviewPanelWidth(availableWidth: number): number {
  const raw = readItem(PANEL_WIDTH_KEY)
  const parsed = raw ? Number(raw) : NaN
  if (!Number.isFinite(parsed)) return REVIEW_PANEL_DEFAULT_WIDTH
  return clampReviewPanelWidth(parsed, availableWidth)
}

/**
 * Side panel width, clamped against the space the panel's container actually
 * has — on mount and on every window resize, so the panel can never squeeze
 * the PR content below its minimum.
 */
export function useReviewPanelWidth(
  containerRef: React.RefObject<HTMLElement | null>
): [number, (next: number) => void] {
  const [width, setState] = useState(() =>
    typeof window === "undefined"
      ? REVIEW_PANEL_DEFAULT_WIDTH
      : readReviewPanelWidth(window.innerWidth)
  )

  const setWidth = useCallback(
    (next: number) => {
      const available =
        containerRef.current?.parentElement?.clientWidth ??
        (typeof window === "undefined" ? next : window.innerWidth)
      const clamped = clampReviewPanelWidth(next, available)
      setState(clamped)
      writeItem(PANEL_WIDTH_KEY, String(clamped))
    },
    [containerRef]
  )

  const widthRef = useRef(width)
  widthRef.current = width
  useEffect(() => {
    if (typeof window === "undefined") return
    const reclamp = () => setWidth(widthRef.current)
    reclamp()
    window.addEventListener("resize", reclamp)
    return () => window.removeEventListener("resize", reclamp)
  }, [setWidth])

  return [width, setWidth]
}
