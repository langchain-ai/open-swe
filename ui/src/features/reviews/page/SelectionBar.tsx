import { Kbd } from "@langchain/macaw-components/Kbd"
import type { SelectedLineRange } from "@pierre/diffs"
import { ChatTextIcon } from "@phosphor-icons/react/dist/ssr/ChatText"
import { useEffect, useReducer } from "react"

import { useShortcutLabel } from "@/lib/hotkeys"
import { readDiffSelection } from "@/features/agents/utils/diffSelection"
import { AgentMark } from "./AgentMark"
import { ASK_SELECTION_SHORTCUT } from "./useDiffKeys"

export interface TextSelection {
  path: string
  range: SelectedLineRange
  x: number
  y: number
  host: HTMLElement
}

const BAR_WIDTH = 260
const BAR_HEIGHT = 36
const GAP = 8

/** Under the selection and inside the diff pane; nowhere once the selection scrolls out of view. */
function placement(
  selection: TextSelection
): { left: number; top: number } | null {
  // A host scrolled out of the virtualized list is detached; the selection went with it.
  const pane = selection.host
    .closest(".review-code-view")
    ?.getBoundingClientRect()
  if (!pane) return null
  const range = readDiffSelection(selection.host)
  const rect =
    range && range.rangeCount > 0
      ? range.getRangeAt(0).getBoundingClientRect()
      : null
  const anchor =
    rect && rect.height > 0
      ? { x: rect.left, y: rect.bottom }
      : { x: selection.x, y: selection.y }
  if (anchor.y < pane.top || anchor.y > pane.bottom - BAR_HEIGHT) return null
  const minLeft = pane.left + GAP
  const maxLeft = pane.right - BAR_WIDTH - GAP
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
  const askShortcut = useShortcutLabel(ASK_SELECTION_SHORTCUT)
  // Placement reads the live layout, so a scroll or resize only needs a re-render.
  const [, follow] = useReducer((frame: number) => frame + 1, 0)
  useEffect(() => {
    let frame = 0
    const schedule = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(follow)
    }
    window.addEventListener("scroll", schedule, {
      capture: true,
      passive: true,
    })
    window.addEventListener("resize", schedule)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener("scroll", schedule, { capture: true })
      window.removeEventListener("resize", schedule)
    }
  }, [])
  const position = placement(selection)
  if (!position) return null
  return (
    <div
      role="toolbar"
      aria-label="Selected code"
      data-add-to-chat
      onPointerDown={(event) => event.stopPropagation()}
      style={{ left: position.left, top: position.top }}
      className="fixed z-selection-action-bar flex items-center gap-0.5 rounded-lg border border-subtle bg-elevated p-0.5 font-sans text-xs shadow-md"
    >
      <button
        type="button"
        onClick={onAsk}
        className="flex items-center gap-space-2 rounded-md px-space-2 py-space-1 font-medium hover:bg-elevated-hover"
      >
        <AgentMark />
        Ask Open SWE
        <Kbd>{askShortcut}</Kbd>
      </button>
      <button
        type="button"
        onClick={onComment}
        className="flex items-center gap-space-2 rounded-md px-space-2 py-space-1 hover:bg-elevated-hover"
      >
        <ChatTextIcon className="size-3.5" weight="regular" />
        Comment
      </button>
    </div>
  )
}
