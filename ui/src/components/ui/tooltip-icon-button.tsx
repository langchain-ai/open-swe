import type * as React from "react"

import { IconButton } from "@/components/ui/button"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

type IconButtonProps = React.ComponentProps<typeof IconButton>
type TooltipSide = React.ComponentProps<typeof TooltipPopup>["side"]

/** Icon-only button whose `label` is both its accessible name and its tooltip. */
function TooltipIconButton({
  label,
  tooltip,
  side,
  variant = "ghost",
  size = "icon-sm",
  className,
  ...props
}: Omit<IconButtonProps, "aria-label" | "title"> & {
  label: string
  tooltip?: React.ReactNode
  side?: TooltipSide
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <IconButton
            aria-label={label}
            className={cn(
              variant === "ghost" &&
                "text-muted-foreground hover:text-foreground",
              className
            )}
            size={size}
            type="button"
            variant={variant}
            {...props}
          />
        }
      />
      <TooltipPopup side={side}>{tooltip ?? label}</TooltipPopup>
    </Tooltip>
  )
}

export { TooltipIconButton }
