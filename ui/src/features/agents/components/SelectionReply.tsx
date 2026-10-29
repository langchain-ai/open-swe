import { Input } from "@langchain/macaw-components/Input"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Button } from "@langchain/macaw-components/Button"
import { ArrowBendUpLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowBendUpLeft"
import { useEffect, useEffectEvent, useReducer, useRef, useState } from "react"
import type { RefObject } from "react"

import {
  readDiffSelection,
  selectedRangeFromDiff,
} from "@/features/agents/utils/diffSelection"
import {
  SELECTION_LIMIT,
  normalizeSelection,
  passageAround,
} from "@/features/agents/utils/passageReply"
import type {
  PassageReply,
  PassageSource,
} from "@/features/agents/utils/passageReply"

const BUTTON = { width: 76, height: 30 }
const POPOVER = { width: 320, height: 96 }
const GAP = 6
/** Keeps the passage marked once focus moves to the reply field and the selection goes. */
const HIGHLIGHT = "selection-reply"
const CHROME = 'button, [role="button"], [aria-hidden="true"]'
/** How far up from the selection to look for a line of context on each side. */
const CONTEXT_ANCESTORS = 6

/** A passage the reader selected, and how to turn a comment on it into a reply. */
export interface Passage {
  range: Range
  preview: string
  reply: (comment: string) => PassageReply
}

function isBlock(element: Element): boolean {
  return !getComputedStyle(element).display.startsWith("inline")
}

/** `root`'s text as it reads on screen, a line per block, and where `range` falls in it. */
function renderedText(
  root: Element,
  range: Range
): { text: string; start: number; end: number } {
  let text = ""
  let start = -1
  let end = -1
  // Controls such as timestamps and fold toggles are interface, not what was said.
  const walker = document.createTreeWalker(
    root,
    NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,
    (node) =>
      node instanceof Element &&
      node.matches(CHROME) &&
      !node.contains(range.startContainer) &&
      !node.contains(range.endContainer)
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT
  )
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node instanceof Element) {
      if (isBlock(node) && text && !text.endsWith("\n")) text += "\n"
      continue
    }
    if (start === -1)
      if (node === range.startContainer) start = text.length + range.startOffset
      else if (range.comparePoint(node, 0) >= 0) start = text.length
    if (end === -1)
      if (node === range.endContainer) end = text.length + range.endOffset
      else if (range.comparePoint(node, 0) > 0) end = text.length
    text += node.textContent ?? ""
  }
  return {
    text,
    start: Math.max(0, start),
    end: end === -1 ? text.length : end,
  }
}

/** The selection with a line of its surroundings on each side, climbing until there is one. */
function textAround(
  range: Range,
  container: HTMLElement
): Pick<PassageReply, "before" | "selection" | "after"> {
  const common = range.commonAncestorContainer
  let root =
    common instanceof Element ? common : (common.parentElement ?? container)
  for (let level = 0; ; level++) {
    const { text, start, end } = renderedText(root, range)
    const passage = passageAround(text, start, end)
    const framed = passage.before.includes("\n") && passage.after.includes("\n")
    if (
      framed ||
      level === CONTEXT_ANCESTORS ||
      root === container ||
      !root.parentElement
    )
      return passage
    root = root.parentElement
  }
}

/** A text selection inside `container`, such as the transcript or a terminal. */
export function textPassage(
  source: PassageSource = "conversation"
): (container: HTMLElement) => Passage | null {
  return (container) => {
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed || selection.rangeCount === 0)
      return null
    const range = selection.getRangeAt(0)
    if (!container.contains(range.commonAncestorContainer)) return null
    const preview = normalizeSelection(selection.toString())
    if (!preview) return null
    const around = textAround(range, container)
    return {
      range: range.cloneRange(),
      preview,
      reply: (comment) => ({ source, ...around, comment }),
    }
  }
}

/** Offset of the start of 1-based `line` in `text`, or its length past the end. */
function lineOffset(text: string, line: number): number {
  let offset = 0
  for (let n = 1; n < line; n++) {
    const next = text.indexOf("\n", offset)
    if (next === -1) return text.length
    offset = next + 1
  }
  return offset
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
      const preview = normalizeSelection(selection.toString())
      if (!lines || !preview) continue
      const first = Math.min(lines.start, lines.end)
      const last = Math.max(lines.start, lines.end)
      const from = lineOffset(contents, first)
      const firstEnd = lineOffset(contents, first + 1)
      // Pin the selection inside its lines; fall back to the whole lines when
      // the rendered text doesn't match the file's verbatim.
      const at = contents.indexOf(preview, from)
      const [start, end] =
        at !== -1 && at < firstEnd
          ? [at, at + preview.length]
          : [from, lineOffset(contents, last + 1)]
      const around = passageAround(contents, start, end)
      return {
        range: range.cloneRange(),
        preview,
        reply: (comment) => ({
          source: "file",
          file: path,
          lines: first === last ? `${first}` : `${first}-${last}`,
          ...around,
          comment,
        }),
      }
    }
    return null
  }
}

const conversationPassage = textPassage("conversation")

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
 * field for what they want to say about it, handed back as a PassageReply.
 */
export function SelectionReply({
  containerRef,
  readPassage = conversationPassage,
  onReply,
}: {
  containerRef: RefObject<HTMLElement | null>
  readPassage?: (container: HTMLElement) => Passage | null
  onReply: (reply: PassageReply) => void
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
      <Button
        color="secondary"
        variant="plain"
        size="sm"
        aria-label="Reply"
        type="button"
        data-selection-reply
        // Keep the selection: focus moving to the button would clear it.
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          markPassage(offer.passage.range)
          setEditing(true)
        }}
        style={{ left: position.left, top: position.top, width: BUTTON.width }}
        className="fixed z-selection-action-bar h-[30px] justify-center gap-space-1 rounded-lg border border-subtle bg-elevated font-sans font-medium text-primary shadow-md hover:bg-elevated-hover"
      >
        <ArrowBendUpLeftIcon className="size-3.5" weight="regular" />
        Reply
      </Button>
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
        <Input
          inputContainerClassName="border-strong/70 [&:hover:not(:focus-within)]:border-strong"
          size="sm"
          debounceMs={0}
          ref={inputRef}
          aria-label="Your reply"
          placeholder="Reply to Open SWE"
          maxLength={SELECTION_LIMIT}
          value={comment}
          onChange={setComment}
          className="min-w-0 flex-1"
        />
        <IconButton
          color="secondary"
          variant="plain"
          size="sm"
          icon={ArrowBendUpLeftIcon}
          iconWeight="regular"
          label="Add reply"
          tooltipProps={{ disabled: true }}
          type="submit"
        />
      </div>
    </form>
  )
}
