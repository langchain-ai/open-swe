import { memo, useCallback, useEffect, useRef, useState } from "react"
import { Check, Copy } from "lucide-react"
import type { ComponentProps } from "react"

import { IconButton } from "@/components/ui/button"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

type CopyButtonProps = Omit<
  ComponentProps<typeof IconButton>,
  "children" | "onClick" | "aria-label" | "render"
> & {
  text: string
  label?: string
  iconClassName?: string
}

export const CopyButton = memo(function CopyButton({
  text,
  label = "Copy to clipboard",
  iconClassName = "size-3",
  className,
  size = "icon-xs",
  variant = "ghost",
  ...props
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false)
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (resetTimerRef.current) clearTimeout(resetTimerRef.current)
    },
    []
  )

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      setCopied(false)
      return
    }
    setCopied(true)
    if (resetTimerRef.current) clearTimeout(resetTimerRef.current)
    resetTimerRef.current = setTimeout(() => setCopied(false), 1500)
  }, [text])

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <IconButton
            {...props}
            aria-label={copied ? "Copied" : label}
            className={cn(
              "text-muted-foreground hover:text-foreground",
              className
            )}
            onClick={copy}
            size={size}
            type="button"
            variant={variant}
          />
        }
      >
        {copied ? (
          <Check className={iconClassName} />
        ) : (
          <Copy className={iconClassName} />
        )}
      </TooltipTrigger>
      <TooltipPopup>{copied ? "Copied!" : label}</TooltipPopup>
    </Tooltip>
  )
})
