import { ChevronDownIcon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Menu,
  MenuCheckboxItem,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
} from "@/components/ui/menu"
import { cn } from "@/lib/utils"

export function MultiSelect<T extends string>({
  label,
  placeholder,
  options,
  value,
  onValueChange,
  searchPlaceholder,
  emptyMessage = "No matches",
}: {
  label: string
  placeholder: string
  options: readonly T[]
  value: readonly T[]
  onValueChange: (value: T[]) => void
  searchPlaceholder?: string
  emptyMessage?: string
}) {
  const [query, setQuery] = useState("")
  const needle = query.trim().toLowerCase()
  const shown =
    searchPlaceholder && needle
      ? options.filter((option) => option.toLowerCase().includes(needle))
      : options
  return (
    <Menu
      onOpenChange={(open) => {
        if (!open) setQuery("")
      }}
    >
      <MenuTrigger render={<Button variant="outline" />} aria-label={label}>
        {value.length === 0
          ? placeholder
          : value.length === 1
            ? value[0]
            : `${value.length} selected`}
        <ChevronDownIcon aria-hidden="true" />
      </MenuTrigger>
      <MenuPopup align="start" className="min-w-48">
        {searchPlaceholder && (
          <input
            className={cn(
              "mb-1 w-full rounded-md border border-border bg-background px-2 py-1",
              "text-xs text-foreground outline-none focus:border-ring"
            )}
            aria-label={searchPlaceholder}
            placeholder={searchPlaceholder}
            value={query}
            autoFocus
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              // The menu's typeahead would otherwise pull focus onto whichever
              // item matches the characters being typed here.
              if (event.key !== "Escape" && event.key !== "Tab")
                event.stopPropagation()
            }}
          />
        )}
        {shown.map((option) => (
          <MenuCheckboxItem
            key={option}
            checked={value.includes(option)}
            closeOnClick={false}
            onCheckedChange={(checked) =>
              onValueChange(
                checked
                  ? [...value, option]
                  : value.filter((item) => item !== option)
              )
            }
          >
            {option}
          </MenuCheckboxItem>
        ))}
        {shown.length === 0 && (
          <p className="px-2 py-1 text-xs text-muted-foreground">
            {emptyMessage}
          </p>
        )}
        <MenuSeparator />
        <MenuItem
          disabled={value.length === 0}
          closeOnClick={false}
          onClick={() => onValueChange([])}
        >
          Clear selection
        </MenuItem>
      </MenuPopup>
    </Menu>
  )
}
