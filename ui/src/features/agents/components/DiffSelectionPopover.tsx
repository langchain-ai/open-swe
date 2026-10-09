import { useMemo } from "react"
import type { ReactNode, RefObject } from "react"
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from "@langchain/macaw-components/Popover"

import type { DiffLineSelection } from "@/features/agents/utils/diffSelection"

// Pierre renders lines in a shadow root, which `contains` doesn't cross.
function isInDiff(selection: DiffLineSelection, target: EventTarget | null) {
  if (!(target instanceof Node)) return false
  const root = target.getRootNode()
  const host = root instanceof ShadowRoot ? root.host : target
  return selection.wrapperProps.ref.current?.contains(host) ?? false
}

export function DiffSelectionPopover({
  selection,
  open,
  initialFocus,
  className,
  children,
}: {
  selection: DiffLineSelection
  open?: boolean
  /** Element to focus on open, or `false` to leave focus where it is. */
  initialFocus?: RefObject<HTMLElement | null> | false
  className?: string
  children: ReactNode
}) {
  const anchorRef = useMemo(
    () => ({ current: selection.anchor }),
    [selection.anchor]
  )
  return (
    <Popover
      open={open ?? selection.committed !== null}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) selection.close()
      }}
    >
      <PopoverAnchor virtualRef={anchorRef} />
      <PopoverContent
        side="bottom"
        align="start"
        className={className}
        onOpenAutoFocus={(event) => {
          if (initialFocus === undefined) return
          event.preventDefault()
          if (initialFocus) initialFocus.current?.focus()
        }}
        // Presses inside the diff start a new selection there instead.
        onInteractOutside={(event) => {
          if (isInDiff(selection, event.target)) event.preventDefault()
        }}
      >
        {children}
      </PopoverContent>
    </Popover>
  )
}
