import type { ComponentProps } from "react"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ChevronDown } from "@/components/glyphs"
import { cn } from "@/lib/utils"

/** The reference composer's foot chip: quiet meta ink on the compact rung. */
const COMPOSER_CHIP_CLASS =
  "min-w-0 gap-1 px-1.5 font-normal text-meta text-ink-subtle hover:text-ink aria-expanded:text-ink"

/**
 * A row in a composer-owned list popup (model, workspace, branch, mentions).
 * Same geometry as a DropdownMenuItem; the highlight is instant so arrow keys
 * never trail a colour ease.
 */
export const COMPOSER_POPUP_ROW_CLASS =
  "flex w-full items-center gap-1.5 rounded-badge px-1.5 py-1 text-left text-label text-ink outline-none select-none"

/** A control in the composer's foot or target tray (model, attach, run target). */
export function ComposerControl({
  className,
  size = "compact",
  variant = "ghost",
  ...props
}: ComponentProps<typeof Button>) {
  return (
    <Button
      className={cn(COMPOSER_CHIP_CLASS, className)}
      size={size}
      variant={variant}
      {...props}
    />
  )
}

export function ComposerControlChevron() {
  return <Icon icon={ChevronDown} size="sm" className="text-ink-subtle" />
}
