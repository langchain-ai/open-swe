import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

/**
 * Where every note sits on its line: pinned to the left edge and no wider
 * than the visible pane, so a long code line scrolling sideways never pushes
 * a comment out of view.
 */
export function NoteFrame({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn("sticky left-0 px-space-3 py-space-1 font-sans", className)}
      style={{
        maxWidth: "min(784px, calc(var(--review-pane-width, 100vw) - 72px))",
      }}
    >
      {children}
    </div>
  )
}
