import { QuotesIcon } from "@phosphor-icons/react/dist/ssr/Quotes"
import { useEffect, useReducer, useState } from "react"
import type { RefObject } from "react"

import { normalizeQuote } from "@/features/agents/utils/transcriptQuote"

const BUTTON_WIDTH = 84
const BUTTON_HEIGHT = 30
const GAP = 6

interface TranscriptSelection {
  range: Range
  container: HTMLElement
}

/** The reader's selection, when it holds text and sits wholly inside the transcript. */
function transcriptSelection(
  container: HTMLElement | null
): TranscriptSelection | null {
  const selection = window.getSelection()
  if (!container || !selection || selection.isCollapsed) return null
  if (selection.rangeCount === 0) return null
  const range = selection.getRangeAt(0)
  if (!container.contains(range.commonAncestorContainer)) return null
  return normalizeQuote(selection.toString()) ? { range, container } : null
}

/** Above the selection, or below it near the top edge; nowhere once it scrolls out of view. */
function placement({
  range,
  container,
}: TranscriptSelection): { left: number; top: number } | null {
  const pane = container.getBoundingClientRect()
  const rect = range.getBoundingClientRect()
  if (rect.height === 0 || rect.bottom < pane.top || rect.top > pane.bottom)
    return null
  const above = rect.top - BUTTON_HEIGHT - GAP
  const top = above >= pane.top ? above : rect.bottom + GAP
  if (top + BUTTON_HEIGHT > pane.bottom) return null
  const left = rect.left + rect.width / 2 - BUTTON_WIDTH / 2
  return {
    left: Math.max(
      pane.left + GAP,
      Math.min(left, pane.right - BUTTON_WIDTH - GAP)
    ),
    top,
  }
}

/** Offers to quote whatever the reader highlights in the transcript into their next message. */
export function TranscriptQuoteButton({
  containerRef,
  onQuote,
}: {
  containerRef: RefObject<HTMLElement | null>
  onQuote: (text: string) => void
}) {
  const [selected, setSelected] = useState<TranscriptSelection | null>(null)
  // Placement reads the live layout, so a scroll or resize only needs a re-render.
  const [, follow] = useReducer((frame: number) => frame + 1, 0)

  useEffect(() => {
    // Wait for the drag to finish so the button doesn't chase the pointer.
    let selecting = false
    const read = () => setSelected(transcriptSelection(containerRef.current))
    const ownsEvent = (event: Event) =>
      event.target instanceof Element &&
      event.target.closest("[data-transcript-quote]") !== null
    const onPointerDown = (event: PointerEvent) => {
      if (ownsEvent(event)) return
      selecting = true
      setSelected(null)
    }
    const onPointerUp = (event: PointerEvent) => {
      if (ownsEvent(event)) return
      selecting = false
      // A click that collapses the selection settles after pointerup.
      requestAnimationFrame(read)
    }
    const onSelectionChange = () => {
      if (!selecting) read()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelected(null)
    }
    let frame = 0
    const onLayout = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(follow)
    }
    document.addEventListener("pointerdown", onPointerDown)
    document.addEventListener("pointerup", onPointerUp)
    document.addEventListener("selectionchange", onSelectionChange)
    document.addEventListener("keydown", onKeyDown)
    window.addEventListener("scroll", onLayout, {
      capture: true,
      passive: true,
    })
    window.addEventListener("resize", onLayout)
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener("pointerdown", onPointerDown)
      document.removeEventListener("pointerup", onPointerUp)
      document.removeEventListener("selectionchange", onSelectionChange)
      document.removeEventListener("keydown", onKeyDown)
      window.removeEventListener("scroll", onLayout, { capture: true })
      window.removeEventListener("resize", onLayout)
    }
  }, [containerRef])

  const position = selected ? placement(selected) : null
  if (!position) return null
  return (
    <button
      type="button"
      data-transcript-quote
      // Keep the selection: focus moving to the button would clear it.
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => {
        const selection = window.getSelection()
        const text = normalizeQuote(selection?.toString() ?? "")
        selection?.removeAllRanges()
        setSelected(null)
        if (text) onQuote(text)
      }}
      style={{ left: position.left, top: position.top, width: BUTTON_WIDTH }}
      className="fixed z-selection-action-bar flex h-[30px] items-center justify-center gap-space-1 rounded-lg border border-subtle bg-elevated font-sans text-xs font-medium text-primary shadow-md hover:bg-elevated-hover"
    >
      <QuotesIcon className="size-3.5" weight="regular" />
      Quote
    </button>
  )
}
