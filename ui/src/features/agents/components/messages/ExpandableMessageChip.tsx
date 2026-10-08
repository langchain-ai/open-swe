import { ChevronRight } from "lucide-react"
import type { ReactNode } from "react"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"

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
  return (
    <Collapsible>
      <div className="flex max-w-full items-center gap-1.5">
        <CollapsibleTrigger
          data-testid={testId}
          aria-label={accessibleLabel}
          className="group/chip flex min-w-0 items-center gap-1.5 rounded-full border border-default bg-surface-level-2/50 px-2.5 py-1 text-[11px] text-secondary transition-colors hover:bg-surface-level-1-hover/30"
        >
          <ChevronRight className="size-3 shrink-0 group-data-panel-open/chip:rotate-90" />
          {label}
        </CollapsibleTrigger>
        {actions}
      </div>
      <CollapsibleContent>{children}</CollapsibleContent>
    </Collapsible>
  )
}
