import { TextAlignLeftIcon } from "@phosphor-icons/react"

import { Toggle } from "@/components/ui/toggle"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { useDiffWrap } from "@/features/agents/utils/diffUtils"
import { cn } from "@/lib/utils"

export function DiffWrapToggle({ className }: { className?: string }) {
  const [wrap, setWrap] = useDiffWrap()

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Toggle
            size="sm"
            pressed={wrap}
            onPressedChange={setWrap}
            aria-label="Wrap lines"
            className={cn(
              "px-0 text-muted-foreground/70 aria-pressed:text-foreground",
              className
            )}
          />
        }
      >
        <TextAlignLeftIcon className="size-3.5" />
      </TooltipTrigger>
      <TooltipPopup>Wrap lines</TooltipPopup>
    </Tooltip>
  )
}
