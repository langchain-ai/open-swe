import { CheckIcon, CopyIcon } from "@phosphor-icons/react"
import type * as React from "react"

import { TooltipIconButton } from "@/components/ui/tooltip-icon-button"
import { useCopyToClipboard } from "@/lib/useCopyToClipboard"

export function CopyButton({
  text,
  label = "Copy to clipboard",
  size = "icon-xs",
  className,
  side,
}: {
  text: string
  label?: string
  size?: React.ComponentProps<typeof TooltipIconButton>["size"]
  className?: string
  side?: React.ComponentProps<typeof TooltipIconButton>["side"]
}) {
  const { copied, copy } = useCopyToClipboard()
  return (
    <TooltipIconButton
      className={className}
      label={label}
      onClick={() => void copy(text)}
      side={side}
      size={size}
      tooltip={copied ? "Copied" : label}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
    </TooltipIconButton>
  )
}
