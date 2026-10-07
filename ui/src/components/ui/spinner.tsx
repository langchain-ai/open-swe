import { CircleNotchIcon } from "@phosphor-icons/react"
import type { IconProps } from "@phosphor-icons/react"

import { cn } from "@/lib/utils"

function Spinner({ className, ...props }: IconProps) {
  return (
    <CircleNotchIcon
      data-slot="spinner"
      role="status"
      aria-label="Loading"
      className={cn("size-3.5 shrink-0 animate-spin", className)}
      {...props}
    />
  )
}

export { Spinner }
