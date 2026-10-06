import { useState } from "react"

import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { SearchInput } from "@langchain/gtm-platform-design-system/ui/search-input"

import { ChevronDown } from "@/components/glyphs"

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
    <DropdownMenu
      onOpenChange={(open) => {
        if (!open) setQuery("")
      }}
    >
      <DropdownMenuTrigger
        render={<Button variant="outline" size="compact" />}
        aria-label={label}
      >
        {value.length === 0
          ? placeholder
          : value.length === 1
            ? value[0]
            : `${value.length} selected`}
        <Icon icon={ChevronDown} size="sm" className="text-ink-subtle" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-48">
        {searchPlaceholder && (
          <div
            className="pb-1"
            onKeyDown={(event) => {
              // The menu's typeahead would otherwise pull focus onto whichever
              // item matches the characters being typed here.
              if (event.key !== "Escape" && event.key !== "Tab")
                event.stopPropagation()
            }}
          >
            <SearchInput
              label={searchPlaceholder}
              placeholder={searchPlaceholder}
              value={query}
              autoFocus
              onValueChange={setQuery}
            />
          </div>
        )}
        {shown.map((option) => (
          <DropdownMenuCheckboxItem
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
          </DropdownMenuCheckboxItem>
        ))}
        {shown.length === 0 && (
          <p className="px-2 py-1 text-meta text-ink-subtle">{emptyMessage}</p>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={value.length === 0}
          closeOnClick={false}
          onClick={() => onValueChange([])}
        >
          Clear selection
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
