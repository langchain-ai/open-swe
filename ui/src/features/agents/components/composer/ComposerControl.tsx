import { CaretDownIcon } from "@langchain/macaw-components/icons"
import type { IconComponent } from "@langchain/macaw-components/Icon"

export const COMPOSER_TRIGGER_CLASS_NAME =
  "flex items-center gap-space-1 text-secondary transition-opacity outline-none hover:opacity-80 focus-visible:ring-2 focus-visible:ring-focus disabled:cursor-default disabled:opacity-60"

/** Trailing caret on the composer's dropdown triggers (run target, repo, branch). */
export function ComposerControlChevron() {
  return (
    <CaretDownIcon
      aria-hidden="true"
      className="-mx-0.5 size-3 shrink-0 text-icon-secondary opacity-70"
      weight="bold"
    />
  )
}

export function ComposerTriggerIcon({ icon: Icon }: { icon: IconComponent }) {
  return <Icon className="size-3.5 shrink-0" weight="regular" />
}
