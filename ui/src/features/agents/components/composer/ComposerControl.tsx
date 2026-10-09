import { CaretDownIcon, CheckIcon } from "@langchain/macaw-components/icons"
import {
  DropdownMenuItem,
  DropdownMenuLabel,
} from "@langchain/macaw-components/DropdownMenu"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

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

export function ComposerMenuLabel({ children }: { children: ReactNode }) {
  return (
    <DropdownMenuLabel className="px-space-2 py-space-1 text-xxs text-tertiary">
      {children}
    </DropdownMenuLabel>
  )
}

/** A menu row: leading icon, label, and a check when it is the current choice. */
export function ComposerMenuOption({
  icon: Icon,
  selected = false,
  destructive = false,
  disabled,
  title,
  trailing,
  onSelect,
  children,
}: {
  icon?: IconComponent
  selected?: boolean
  destructive?: boolean
  disabled?: boolean
  title?: string
  trailing?: ReactNode
  onSelect?: () => void
  children: ReactNode
}) {
  return (
    <DropdownMenuItem
      className={cn(
        "gap-space-2 text-xs",
        destructive && "text-error-secondary focus:bg-error-subtle"
      )}
      disabled={disabled}
      onSelect={onSelect}
      size="sm"
      title={title}
    >
      {Icon ? (
        <Icon
          className={cn(
            "size-3.5 shrink-0",
            destructive ? "text-icon-error" : "text-icon-secondary"
          )}
          weight="regular"
        />
      ) : null}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {trailing}
      {selected ? (
        <CheckIcon
          className="size-3.5 shrink-0 text-icon-secondary"
          weight="regular"
        />
      ) : null}
    </DropdownMenuItem>
  )
}
