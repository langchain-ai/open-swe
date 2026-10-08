import { useEffect, useState } from "react"
import type { SelectedLineRange } from "@pierre/diffs"
import { ChatTextIcon } from "@phosphor-icons/react"

import { Kbd } from "@/components/ui/kbd"
import { readDiffSelection } from "@/features/agents/utils/diffSelection"
import { AgentMark } from "./AgentMark"

export interface TextSelection {
  path: string
  range: SelectedLineRange
  x: number
  y: number
  host: HTMLElement
}

const BAR_WIDTH = 260
const GAP = 8

/** Under the selection and inside the diff pane; nowhere once the selection scrolls out of view. */
function placement(
  selection: TextSelection
): { left: number; top: number } | null {
  const pane = selection.host
    .closest(".review-code-view")
    ?.getBoundingClientRect()
  const range = readDiffSelection(selection.host)
  const rect =
    range && range.rangeCount > 0
      ? range.getRangeAt(0).getBoundingClientRect()
      : null
  const anchor =
    rect && rect.height > 0
      ? { x: rect.left, y: rect.bottom }
      : { x: selection.x, y: selection.y }
  if (pane && (anchor.y < pane.top || anchor.y > pane.bottom - 36)) return null
  const minLeft = (pane?.left ?? 0) + GAP
  const maxLeft = (pane?.right ?? window.innerWidth) - BAR_WIDTH - GAP
  return {
    left: Math.max(minLeft, Math.min(anchor.x, maxLeft)),
    top: anchor.y + GAP,
  }
}

/** Two things to do with highlighted code: ask the agent, or comment for the author. */
export function SelectionBar({
  selection,
  onAsk,
  onComment,
}: {
  selection: TextSelection
  onAsk: () => void
  onComment: () => void
}) {
  const [position, setPosition] = useState(() => placement(selection))
  const [tracked, setTracked] = useState(selection)
  if (tracked !== selection) {
    setTracked(selection)
    setPosition(placement(selection))
  }
  useEffect(() => {
    let frame = 0
    const follow = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => setPosition(placement(selection)))
    }
    window.addEventListener("scroll", follow, { capture: true, passive: true })
    window.addEventListener("resize", follow)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener("scroll", follow, { capture: true })
      window.removeEventListener("resize", follow)
    }
  }, [selection])
  if (!position) return null
  return (
    <div
      role="toolbar"
      aria-label="Selected code"
      data-add-to-chat
      onPointerDown={(event) => event.stopPropagation()}
      style={{ left: position.left, top: position.top }}
      className="fixed z-50 flex items-center gap-0.5 rounded-lg border border-border bg-popover p-0.5 font-sans text-xs shadow-lg"
    >
      <button
        type="button"
        onClick={onAsk}
        className="flex items-center gap-1.5 rounded-md px-2 py-1 font-medium hover:bg-accent"
      >
        <AgentMark />
        Ask Open SWE
        <Kbd>⌘L</Kbd>
      </button>
      <button
        type="button"
        onClick={onComment}
        className="flex items-center gap-1.5 rounded-md px-2 py-1 hover:bg-accent"
      >
        <ChatTextIcon className="size-3.5" />
        Comment
      </button>
    </div>
  )
}
