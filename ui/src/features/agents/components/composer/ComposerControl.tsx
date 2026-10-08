import { ChevronDown } from "lucide-react"

import type { ComponentProps } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const composerControlClassName =
  "h-7 min-h-7 gap-1.5 px-2 text-tertiary transition-none hover:text-primary"

/** A button in the composer's bottom control row (model, attach). */
export function ComposerControl({
  className,
  size = "sm",
  variant = "ghost",
  ...props
}: ComponentProps<typeof Button>) {
  return (
    <Button
      className={cn(composerControlClassName, className)}
      size={size}
      variant={variant}
      {...props}
    />
  )
}

export function ComposerControlChevron() {
  return (
    <ChevronDown
      aria-hidden="true"
      className="-mx-0.5 size-3 shrink-0 text-secondary opacity-70"
      strokeWidth={2.25}
    />
  )
}
