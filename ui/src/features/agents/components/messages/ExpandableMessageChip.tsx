import { Button } from "@langchain/macaw-components/Button"
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
        <Button
          color="secondary"
          variant="plain"
          size="xs"
          type="button"
          data-testid={testId}
          aria-label={accessibleLabel}
          aria-labelledby={accessibleLabel ? undefined : `${contentId}-label`}
          aria-expanded={open}
          aria-controls={open ? contentId : undefined}
          onClick={() => setOpen((value) => !value)}
          className="min-w-0 shrink gap-space-2 rounded-full border border-default bg-surface-level-2 px-space-2 py-space-1 text-secondary hover:bg-surface-level-2-hover hover:text-primary focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
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
          <span id={`${contentId}-label`} className="min-w-0 truncate">
            {label}
          </span>
        </Button>
        {actions}
      </div>
      {open && <div id={contentId}>{children}</div>}
    </div>
  )
}
