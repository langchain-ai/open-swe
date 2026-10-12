import { CaretRightIcon } from "@langchain/macaw-components/icons"
import { useId, useState } from "react"
import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

export function ExpandableMessageChip({
  label,
  actions,
  children,
  testId,
  accessibleLabel,
}: {
  label: ReactNode
  actions?: ReactNode
  children: ReactNode
  testId?: string
  accessibleLabel?: string
}) {
  const [open, setOpen] = useState(false)
  const contentId = useId()

  return (
    <div>
      <div className="flex max-w-full items-center gap-space-1">
        <button
          type="button"
          data-testid={testId}
          aria-label={accessibleLabel}
          aria-expanded={open}
          aria-controls={open ? contentId : undefined}
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 items-center gap-space-2 rounded-full border border-default bg-surface-level-2 px-space-2 py-space-1 text-xxs text-secondary transition-colors duration-normal hover:bg-surface-level-2-hover hover:text-primary focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
        >
          <CaretRightIcon
            size={12}
            weight="bold"
            aria-hidden
            className={cn(
              "shrink-0 transition-transform duration-fast",
              open && "rotate-90"
            )}
          />
          {label}
        </button>
        {actions}
      </div>
      {open && <div id={contentId}>{children}</div>}
    </div>
  )
}
