import type { ResizableWidthHandlers } from "@/lib/useResizableWidth"
import { cn } from "@/lib/utils"

interface Props {
  handlers: ResizableWidthHandlers
  className?: string
}

/**
 * Seam handle for a right-anchored panel, drawn like the app rail's: a thin
 * strip straddling the hairline that only tints under the pointer.
 */
export function RightPanelResizeHandle({ handlers, className }: Props) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize panel"
      className={cn(
        "absolute inset-y-0 left-0 z-20 w-1.5 -translate-x-1/2 cursor-col-resize touch-none bg-transparent transition-colors duration-fast ease-out-quint select-none hover:bg-line-strong/40 active:bg-line-strong/60 motion-reduce:transition-none",
        className
      )}
      {...handlers}
    />
  )
}
