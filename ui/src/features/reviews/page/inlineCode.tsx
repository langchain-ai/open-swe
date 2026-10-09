import type { ReactNode } from "react"

/** A one-line title with `backtick` spans as code, without the block Markdown renderer. */
export function InlineCode({ text }: { text: string }): ReactNode {
  return text.split(/(`[^`]+`)/g).map((part, i) =>
    part.length >= 2 && part.startsWith("`") && part.endsWith("`") ? (
      <code
        key={i}
        className="rounded-xs bg-surface-level-3 px-space-1 py-px font-mono text-[0.88em]"
      >
        {part.slice(1, -1)}
      </code>
    ) : (
      <span key={i}>{part}</span>
    )
  )
}
