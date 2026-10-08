import type { SelectedLineRange } from "@pierre/diffs"
import { ChatTextIcon } from "@phosphor-icons/react"

import { Kbd } from "@/components/ui/kbd"
import { AgentMark } from "./AgentMark"

export interface TextSelection {
  path: string
  range: SelectedLineRange
  x: number
  y: number
  host: HTMLElement
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
  return (
    <div
      role="toolbar"
      aria-label="Selected code"
      data-add-to-chat
      onPointerDown={(event) => event.stopPropagation()}
      style={{ left: selection.x, top: selection.y + 14 }}
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
