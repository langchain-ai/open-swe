import { TextAlignLeftIcon } from "@phosphor-icons/react"

import { useDiffWrap } from "@/features/agents/utils/diffUtils"
import { cn } from "@/lib/utils"

export function DiffWrapToggle({ className }: { className?: string }) {
  const [wrap, setWrap] = useDiffWrap()

  return (
    <button
      type="button"
      onClick={() => setWrap(!wrap)}
      aria-label="Wrap lines"
      aria-pressed={wrap}
      title="Wrap lines"
      className={cn(
        "flex size-6 items-center justify-center rounded text-[var(--ui-text-dim)] transition-colors hover:text-[var(--ui-text)]",
        wrap && "bg-[var(--ui-accent-bubble)] text-[var(--ui-text)]",
        className
      )}
    >
      <TextAlignLeftIcon className="size-3.5" />
    </button>
  )
}
