import type { ResizableWidthHandlers } from "@/lib/useResizableWidth"
import { cn } from "@/lib/utils"

interface Props {
  handlers: ResizableWidthHandlers
  className?: string
}

/**
 * Hit target for resizing a right-anchored panel via its left edge.
 *
 * - Sits on top of the panel's border with a 4px overlap on each side so the
 *   user can grab a few pixels off the edge without aiming.
 * - Visual indicator is a 1px line that lights up on hover/active to mirror
 *   VS Code / Cursor.
 */
export function RightPanelResizeHandle({ handlers, className }: Props) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      className={cn(
        "group absolute inset-y-0 -left-1 z-resize-handle w-2 cursor-col-resize select-none",
        className
      )}
      {...handlers}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute inset-y-0 left-1/2 w-0 -translate-x-1/2 border-l border-transparent transition-colors duration-normal group-hover:border-default group-active:border-brand"
      />
    </div>
  )
}
