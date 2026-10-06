import type { ReactNode } from "react"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@langchain/gtm-platform-design-system/ui/tooltip"
import { cn } from "@/lib/utils"

/**
 * The icon control of the work panes' toolbars: a ghost compact button whose
 * name is its tooltip. A toggle passes `pressed` and reads as selected.
 */
export function PanelIconButton({
  label,
  title,
  pressed,
  disabled,
  onClick,
  className,
  children,
}: {
  label: string
  /** Tooltip text when it should say more than the accessible name. */
  title?: string
  pressed?: boolean
  disabled?: boolean
  onClick: () => void
  className?: string
  children: ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            aria-label={label}
            aria-pressed={pressed}
            className={cn(
              "text-ink-subtle hover:text-ink",
              pressed && "bg-selected text-ink",
              className
            )}
            disabled={disabled}
            onClick={onClick}
            size="icon-sm"
            type="button"
            variant="ghost"
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{title ?? label}</TooltipContent>
    </Tooltip>
  )
}
