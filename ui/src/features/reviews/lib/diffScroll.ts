/**
 * Scrolling a virtualized diff to a target that keeps moving.
 *
 * Rows above the target mount, measure and expand while the scroll is in
 * flight, so a one-shot scroll lands in the wrong place and a smooth scroll
 * races the reflow. Every helper here therefore jumps instantly and then
 * re-asserts the target until the layout settles (`jumpAndHold`), backing off
 * the moment the reader scrolls themselves.
 */

import type { FileDiff as CoreFileDiff, SelectionSide } from "@pierre/diffs"
import type { useVirtualizer } from "@pierre/diffs/react"

/** Breathing room left above a block/file when it's scrolled to the top. */
export const SCROLL_TOP_GAP = 8

/** How long to keep re-asserting a scroll target after the initial jump. */
const SCROLL_HOLD_TIMEOUT_MS = 700

/**
 * Frames to keep polling for an annotation node to mount before giving up —
 * its diff rows only render once virtualization windows them in.
 */
export const FINDING_SCROLL_MAX_FRAMES = 120

/**
 * The virtualizer instance returned by useVirtualizer(); exposes
 * getOffsetInScrollContainer for accurate scroll targeting.
 */
export type DiffVirtualizer = NonNullable<ReturnType<typeof useVirtualizer>>

/** A mounted Pierre diff plus the host element it rendered into. */
export interface RegisteredDiffInstance<T> {
  host: HTMLElement
  instance: CoreFileDiff<T>
}

interface PositionedDiffInstance {
  getLinePosition: (
    lineNumber: number,
    side?: SelectionSide
  ) => { top: number; height: number } | undefined
}

function hasLinePosition<T>(
  instance: CoreFileDiff<T>
): instance is CoreFileDiff<T> & PositionedDiffInstance {
  return (
    typeof (instance as { getLinePosition?: unknown }).getLinePosition ===
    "function"
  )
}

export function clampScrollTop(scroller: HTMLElement, top: number): number {
  return Math.max(
    0,
    Math.min(top, scroller.scrollHeight - scroller.clientHeight)
  )
}

/**
 * Jump the scroller to getTarget() instantly, then re-assert that target each
 * time the scroll content reflows (off-screen cards mounting, files expanding,
 * annotation cards measuring) — a ResizeObserver is the real "layout settled"
 * signal, replacing fixed frame-budget correction loops. Bails the moment the
 * user scrolls so we never fight them, and disconnects after a short ceiling.
 * Returns a stop fn to cancel the hold.
 */
export function jumpAndHold(
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

/**
 * Scroll a file card / group flush to the top of the diff scroller (fallback
 * when no virtualizer geometry is available), respecting the element's
 * scroll-margin-top. Returns a stop fn to cancel the hold.
 */
export function scrollCardToTop(
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

/**
 * Scroll a block / file card flush to the top using the virtualizer's own
 * geometry. getOffsetInScrollContainer returns the element's absolute offset
 * within the scroll content; with uniform fixed-height rows (see diffUtils)
 * that offset is stable, so an instant jump lands precisely, and the hold
 * re-reads it as rows above measure. Returns a stop fn to cancel the hold.
 */
export function scrollCardToTopVirtual(
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

/** Absolute scrollTop that centers el within the scroller's viewport. */
export function elementCenterTarget(
  el: HTMLElement,
  scroller: HTMLElement
): number {
  const elementRect = el.getBoundingClientRect()
  const scrollerRect = scroller.getBoundingClientRect()
  const delta =
    elementRect.top -
    scrollerRect.top -
    (scroller.clientHeight - elementRect.height) / 2
  return clampScrollTop(scroller, scroller.scrollTop + delta)
}

/** Centers el and reports how far the scroller moved. */
export function scrollElementToCenter(
  el: HTMLElement,
  scroller: HTMLElement
): number {
  const before = scroller.scrollTop
  const targetTop = elementCenterTarget(el, scroller)
  scroller.scrollTo({ top: targetTop, behavior: "auto" })
  return Math.abs(targetTop - before)
}

/**
 * Center one diff line using the diff instance's own line geometry. False when
 * the instance can't report line positions or hasn't rendered that line yet.
 */
export function scrollDiffLineToCenter<T>(
  target: RegisteredDiffInstance<T>,
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
