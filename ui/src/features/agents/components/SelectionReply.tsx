import { ArrowBendUpLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowBendUpLeft"
import { useEffect, useEffectEvent, useReducer, useRef, useState } from "react"
import type { RefObject } from "react"

import { selectionExcerpts } from "@/features/agents/utils/codeExcerpt"
import {
  readDiffSelection,
  selectedRangeFromDiff,
} from "@/features/agents/utils/diffSelection"
import { normalizeQuote } from "@/features/agents/utils/quoteReply"
import type { QuoteReply } from "@/features/agents/utils/quoteReply"

const BUTTON = { width: 76, height: 30 }
const POPOVER = { width: 320, height: 96 }
const GAP = 6
/** Keeps the passage marked once focus moves to the reply field and the selection goes. */
const HIGHLIGHT = "selection-reply"

/** A passage the reader selected, and how to turn a comment on it into a reply. */
export interface Passage {
  range: Range
  preview: string
  reply: (comment: string) => QuoteReply
}

/** The reader's selection, when it holds text and sits wholly inside `container`. */
export function textPassage(container: HTMLElement): Passage | null {
  const selection = window.getSelection()
  if (!selection || selection.isCollapsed || selection.rangeCount === 0)
    return null
  const range = selection.getRangeAt(0)
  if (!container.contains(range.commonAncestorContainer)) return null
  const quote = normalizeQuote(selection.toString())
  if (!quote) return null
  return {
    range: range.cloneRange(),
    preview: quote,
    reply: (comment) => ({ kind: "text", quote, comment }),
  }
}

/** Selected lines of a file a code view renders, which draws them in a shadow root. */
export function codePassage(
  path: string,
  contents: string
): (container: HTMLElement) => Passage | null {
  return (container) => {
    for (const host of container.querySelectorAll("diffs-container")) {
      const selection = readDiffSelection(host)
      if (!selection || selection.isCollapsed || selection.rangeCount === 0)
        continue
      const range = selection.getRangeAt(0)
      if (range.commonAncestorContainer.getRootNode() !== host.shadowRoot)
        continue
      const lines = selectedRangeFromDiff(host)
      const preview = normalizeQuote(selection.toString())
      if (!lines || !preview) continue
      const [excerpt] = selectionExcerpts(
        path,
        { originalContent: null, modifiedContent: contents },
        lines
      )
      if (!excerpt) continue
      // A file has one side, so its lines need no diff side marker.
      const lineLabel = excerpt.lineLabel.replace(/^R/, "")
      return {
        range: range.cloneRange(),
        preview,
        reply: (comment) => ({
          kind: "code",
          excerpt: { ...excerpt, lineLabel },
          comment,
        }),
      }
    }
    return null
  }
}

interface Offer {
  passage: Passage
  container: HTMLElement
}

/** Next to the passage, above it when there is room; nowhere once it scrolls out of view. */
function placement(
  { passage, container }: Offer,
  size: { width: number; height: number }
): { left: number; top: number } | null {
  const pane = container.getBoundingClientRect()
  const rect = passage.range.getBoundingClientRect()
  if (rect.height === 0 || rect.bottom < pane.top || rect.top > pane.bottom)
    return null
  const above = rect.top - size.height - GAP
  const top = above >= pane.top ? above : rect.bottom + GAP
  if (top + size.height > pane.bottom) return null
  const left = rect.left + rect.width / 2 - size.width / 2
  return {
    left: Math.max(
      pane.left + GAP,
      Math.min(left, pane.right - size.width - GAP)
    ),
    top,
  }
}

function markPassage(range: Range | null) {
  if (typeof CSS === "undefined" || !("highlights" in CSS)) return
  if (range) CSS.highlights.set(HIGHLIGHT, new Highlight(range))
  else CSS.highlights.delete(HIGHLIGHT)
}

/**
 * Offers Reply on whatever the reader highlights inside `containerRef`, then a
 * field for what they want to say about it, handed back as a QuoteReply.
 */
export function SelectionReply({
  containerRef,
  readPassage = textPassage,
  onReply,
}: {
  containerRef: RefObject<HTMLElement | null>
  readPassage?: (container: HTMLElement) => Passage | null
  onReply: (reply: QuoteReply) => void
}) {
  const [offer, setOffer] = useState<Offer | null>(null)
  const [editing, setEditing] = useState(false)
  const [comment, setComment] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)
  // Placement reads the live layout, so a scroll or resize only needs a re-render.
  const [, follow] = useReducer((frame: number) => frame + 1, 0)

  const close = () => {
    markPassage(null)
    setEditing(false)
    setComment("")
    setOffer(null)
  }

  const read = useEffectEvent(() => {
    const container = containerRef.current
    const passage = container ? readPassage(container) : null
    setOffer(passage && container ? { passage, container } : null)
  })

  useEffect(() => {
    // Wait for the drag to finish so the button doesn't chase the pointer.
    let selecting = false
    const ownsEvent = (event: Event) =>
      event
        .composedPath()
        .some(
          (node) =>
            node instanceof Element && node.hasAttribute("data-selection-reply")
        )
    const dismiss = () => {
      markPassage(null)
      setEditing(false)
      setComment("")
      setOffer(null)
    }
    const onPointerDown = (event: PointerEvent) => {
      if (ownsEvent(event)) return
      selecting = true
      dismiss()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss()
    }
    // Touch selection often ends in pointercancel (a long-press or a scroll takes over), not pointerup.
    const onPointerEnd = (event: PointerEvent) => {
      if (ownsEvent(event) || !selecting) return
      selecting = false
      // A click that collapses the selection settles after pointerup.
      requestAnimationFrame(read)
    }
    let frame = 0
    const onLayout = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(follow)
    }
    document.addEventListener("pointerdown", onPointerDown)
    document.addEventListener("pointerup", onPointerEnd)
    document.addEventListener("pointercancel", onPointerEnd)
    document.addEventListener("keydown", onKeyDown)
    window.addEventListener("scroll", onLayout, {
      capture: true,
      passive: true,
    })
    window.addEventListener("resize", onLayout)
    return () => {
      cancelAnimationFrame(frame)
      markPassage(null)
      document.removeEventListener("pointerdown", onPointerDown)
      document.removeEventListener("pointerup", onPointerEnd)
      document.removeEventListener("pointercancel", onPointerEnd)
      document.removeEventListener("keydown", onKeyDown)
      window.removeEventListener("scroll", onLayout, { capture: true })
      window.removeEventListener("resize", onLayout)
    }
  }, [])

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  if (!offer) return null
  const position = placement(offer, editing ? POPOVER : BUTTON)
  if (!position) return null

  if (!editing)
    return (
      <button
        type="button"
        data-selection-reply
        // Keep the selection: focus moving to the button would clear it.
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          markPassage(offer.passage.range)
          setEditing(true)
        }}
        style={{ left: position.left, top: position.top, width: BUTTON.width }}
        className="fixed z-selection-action-bar flex h-[30px] items-center justify-center gap-space-1 rounded-lg border border-subtle bg-elevated font-sans text-xs font-medium text-primary shadow-md hover:bg-elevated-hover"
      >
        <ArrowBendUpLeftIcon className="size-3.5" weight="regular" />
        Reply
      </button>
    )

  const submit = () => {
    onReply(offer.passage.reply(comment.trim()))
    window.getSelection()?.removeAllRanges()
    close()
  }
  return (
    <form
      data-selection-reply
      aria-label="Reply to the selected text"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
      style={{ left: position.left, top: position.top, width: POPOVER.width }}
      className="fixed z-selection-action-bar flex flex-col gap-space-2 rounded-xl border border-subtle bg-elevated p-space-2 font-sans shadow-md"
    >
      <p className="line-clamp-2 border-l-2 border-default pl-space-2 text-xs whitespace-pre-wrap text-secondary">
        {offer.passage.preview}
      </p>
      <div className="flex items-center gap-space-1">
        <input
          ref={inputRef}
          aria-label="Your reply"
          placeholder="Reply to Open SWE"
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          className="min-w-0 flex-1 bg-transparent px-space-1 text-sm text-primary outline-none placeholder:text-tertiary"
        />
        <button
          type="submit"
          aria-label="Add reply"
          className="flex size-6 shrink-0 items-center justify-center rounded-md text-secondary hover:bg-elevated-hover hover:text-primary"
        >
          <ArrowBendUpLeftIcon className="size-3.5" weight="regular" />
        </button>
      </div>
    </form>
  )
}
