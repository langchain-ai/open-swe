import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { PointerEvent as ReactPointerEvent } from "react"
import { areSelectionsEqual } from "@pierre/diffs"
import type { SelectedLineRange, SelectionSide } from "@pierre/diffs"

import { DIFF_VIRTUAL_METRICS } from "@/features/agents/utils/diffUtils"

interface ShadowRootWithSelection {
  getSelection?: () => Selection | null
}

export function readDiffSelection(
  container: Element | null | undefined
): Selection | null {
  const root = container?.shadowRoot
  if (root) {
    const scoped = (root as ShadowRoot & ShadowRootWithSelection).getSelection
    if (typeof scoped === "function") return scoped.call(root)
  }
  return typeof document !== "undefined" ? document.getSelection() : null
}

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

export function selectedRangeFromDiff(
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

type DiffSelectionSource = "lines" | "text"

export interface CommittedDiffSelection {
  range: SelectedLineRange
  source: DiffSelectionSource
}

interface DiffPoint {
  x: number
  y: number
}

interface DiffLineSelectionOptions {
  enabled: boolean
  /** Caller-owned highlight; omit to let the hook own it. */
  selectedLines?: SelectedLineRange | null
  onSelectedLinesChange?: (range: SelectedLineRange | null) => void
  onCommit?: (selection: CommittedDiffSelection) => void
}

function pointIn(
  element: HTMLElement | null,
  event: { clientX: number; clientY: number }
): DiffPoint | null {
  const rect = element?.getBoundingClientRect()
  return rect
    ? { x: event.clientX - rect.left, y: event.clientY - rect.top }
    : null
}

function textRangeIn(element: HTMLElement | null) {
  return selectedRangeFromDiff(element?.querySelector("diffs-container"))
}

export type DiffLineSelection = ReturnType<typeof useDiffLineSelection>

export function useDiffLineSelection({
  enabled,
  selectedLines,
  onSelectedLinesChange,
  onCommit,
}: DiffLineSelectionOptions) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  // Diff-relative, so the popover's anchor stays on the code as it scrolls.
  const downRef = useRef<DiffPoint | null>(null)
  const upRef = useRef<DiffPoint | null>(null)
  const anchorPointRef = useRef<DiffPoint>({ x: 0, y: 0 })
  const [ownSelection, setOwnSelection] = useState<SelectedLineRange | null>(
    null
  )
  const [committed, setCommitted] = useState<CommittedDiffSelection | null>(
    null
  )
  const setSelection = useCallback(
    (range: SelectedLineRange | null) => {
      setOwnSelection(range)
      onSelectedLinesChange?.(range)
    },
    [onSelectedLinesChange]
  )
  const close = useCallback(() => {
    setCommitted(null)
    setSelection(null)
  }, [setSelection])
  const commit = useCallback(
    (source: DiffSelectionSource, range: SelectedLineRange | null) => {
      if (!range) return
      const down = downRef.current
      const up = upRef.current ?? down
      anchorPointRef.current = {
        x: up?.x ?? 0,
        // Below whichever end of the drag is lower, past the line under it.
        y:
          Math.max(down?.y ?? 0, up?.y ?? 0) +
          DIFF_VIRTUAL_METRICS.lineHeight / 2,
      }
      setSelection(range)
      setCommitted({ range, source })
      onCommit?.({ range, source })
    },
    [setSelection, onCommit]
  )
  useEffect(() => {
    if (!enabled) return
    // Window capture: a drag can be released outside the diff, and Pierre
    // reports the selection end from its own document listener.
    const onPointerUp = (event: PointerEvent) => {
      if (downRef.current) upRef.current = pointIn(wrapperRef.current, event)
    }
    window.addEventListener("pointerup", onPointerUp, true)
    return () => window.removeEventListener("pointerup", onPointerUp, true)
  }, [enabled])
  // Controlled `selectedLines` makes Pierre hold a drag as a proposal until
  // it is echoed back, so onLineSelectionChange paints the drag live.
  const diffOptions = useMemo(
    () =>
      enabled
        ? {
            enableLineSelection: true,
            enableGutterUtility: true,
            onGutterUtilityClick: (range: SelectedLineRange) =>
              commit("lines", range),
            onLineSelectionChange: setSelection,
            // A code text highlight is committed on mouseup instead.
            onLineSelectionEnd: (range: SelectedLineRange | null) => {
              if (!textRangeIn(wrapperRef.current)) commit("lines", range)
            },
          }
        : {},
    [enabled, commit, setSelection]
  )
  // Reads refs rather than state so the popover keeps its place while it
  // animates out after closing.
  const anchor = useMemo(
    () => ({
      getBoundingClientRect: () => {
        const rect = wrapperRef.current?.getBoundingClientRect()
        const point = anchorPointRef.current
        return new DOMRect(
          (rect?.left ?? 0) + point.x,
          (rect?.top ?? 0) + point.y,
          0,
          0
        )
      },
      // Floating UI watches this element's scroll ancestors to reposition.
      get contextElement() {
        return wrapperRef.current ?? undefined
      },
    }),
    []
  )
  const wrapperProps = {
    ref: wrapperRef,
    onPointerDownCapture: (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!enabled) return
      downRef.current = pointIn(wrapperRef.current, event)
      upRef.current = null
      // A new press starts a new selection; Pierre repaints any drag.
      close()
    },
    onMouseUp: () => {
      if (enabled) commit("text", textRangeIn(wrapperRef.current))
    },
  }
  const highlighted = selectedLines === undefined ? ownSelection : selectedLines
  return {
    diffOptions,
    wrapperProps,
    selectedLines: highlighted,
    // The caller can clear its highlight on its own (e.g. ⌘L adding it to
    // chat), which ends the commit too.
    committed:
      committed && areSelectionsEqual(committed.range, highlighted ?? undefined)
        ? committed
        : null,
    anchor,
    close,
  }
}
