import { CheckIcon } from "@langchain/macaw-components/icons"
import {
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
} from "@langchain/macaw-components/DropdownMenu"
import type { ComponentProps } from "react"

import { cn } from "@/lib/utils"

/**
 * Macaw ships no checkbox or radio menu items, so this is a plain item that
 * carries the checked state for assistive tech and shows it as a leading tick.
 * `onCheckedChange` toggles without closing the menu, for checklists.
 */
export function MenuCheckItem({
  checked,
  type = "checkbox",
  onCheckedChange,
  onSelect,
  className,
  children,
  ...props
}: Omit<ComponentProps<typeof DropdownMenuItem>, "role"> & {
  checked: boolean
  type?: "checkbox" | "radio"
  onCheckedChange?: (checked: boolean) => void
}) {
  return (
    <DropdownMenuItem
      role={type === "radio" ? "menuitemradio" : "menuitemcheckbox"}
      aria-checked={checked}
      className={cn("gap-space-2", className)}
      onSelect={(event) => {
        onSelect?.(event)
        if (onCheckedChange) {
          event.preventDefault()
          onCheckedChange(!checked)
        }
      }}
      {...props}
    >
      <span className="flex size-3.5 shrink-0 items-center justify-center">
        {checked && <CheckIcon size={14} weight="regular" />}
      </span>
      {children}
    </DropdownMenuItem>
  )
}

/** A labelled set of radio items that stays open while the choice changes. */
export function MenuChoiceGroup<T extends string>({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: T
  onChange: (value: T) => void
  options: ReadonlyArray<{ value: T; label: string }>
}) {
  return (
    <DropdownMenuGroup role="group" aria-label={label}>
      <DropdownMenuLabel className="px-space-2 py-space-1 text-xxs font-medium text-tertiary">
        {label}
      </DropdownMenuLabel>
      {options.map((option) => (
        <MenuCheckItem
          key={option.value}
          type="radio"
          checked={option.value === value}
          onCheckedChange={() => onChange(option.value)}
        >
          {option.label}
        </MenuCheckItem>
      ))}
    </DropdownMenuGroup>
  )
}
