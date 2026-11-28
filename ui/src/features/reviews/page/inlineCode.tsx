import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

/** A one-line title with `backtick` spans as code, without the block Markdown renderer. */
export function InlineCode({
  text,
  className,
}: {
  text: string
  className?: string
}): ReactNode {
  return text.split(/(`[^`]+`)/g).map((part, i) =>
    part.length >= 2 && part.startsWith("`") && part.endsWith("`") ? (
      <code
        key={i}
        className={cn(
          "rounded-xs bg-surface-level-3 px-space-1 py-px font-mono text-[0.88em]",
          className
        )}
      >
        {part.slice(1, -1)}
      </code>
    ) : (
      <span className={className} key={i}>
        {part}
      </span>
    )
  )
}
