import type { ComponentProps, ReactNode } from "react"

import { Popover, PopoverContent } from "@langchain/gtm-platform-design-system/ui/popover"
import type { DiffLineSelection } from "@/features/agents/utils/diffSelection"

// Pierre renders lines in a shadow-control root, which `contains` doesn't cross.
function isInDiff(selection: DiffLineSelection, node: Node) {
  const root = node.getRootNode()
  const host = root instanceof ShadowRoot ? root.host : node
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
  initialFocus?: ComponentProps<typeof PopoverContent>["initialFocus"]
  className?: string
  children: ReactNode
}) {
  return (
    <Popover
      open={open ?? selection.committed !== null}
      onOpenChange={(nextOpen, details) => {
        if (nextOpen) return
        // Presses inside the diff start a new selection there instead.
        const target = details.event?.target
        if (target instanceof Node && isInDiff(selection, target)) return
        selection.close()
      }}
    >
      <PopoverContent
        anchor={selection.anchor}
        side="bottom"
        align="start"
        initialFocus={initialFocus}
        className={className}
      >
        {children}
      </PopoverContent>
    </Popover>
  )
}
