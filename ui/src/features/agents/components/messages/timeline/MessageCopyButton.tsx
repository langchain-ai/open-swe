import { memo, useCallback, useEffect, useRef, useState } from "react"
import { Check, Copy } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

const COPIED_RESET_MS = 1500

export const MessageCopyButton = memo(function MessageCopyButton({
  text,
  className,
}: {
  text: string
  className?: string
}) {
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
      return
    }
    setCopied(true)
    if (resetTimerRef.current) clearTimeout(resetTimerRef.current)
    resetTimerRef.current = setTimeout(() => setCopied(false), COPIED_RESET_MS)
  }, [text])

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            aria-label="Copy message"
            className={cn(
              "text-muted-foreground hover:text-foreground",
              className
            )}
            onClick={copy}
            size="icon-xs"
            type="button"
            variant="ghost"
          />
        }
      >
        {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
      </TooltipTrigger>
      <TooltipPopup>{copied ? "Copied!" : "Copy to clipboard"}</TooltipPopup>
    </Tooltip>
  )
})
