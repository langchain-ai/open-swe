import type { ReactNode } from "react"

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { cn } from "@/lib/utils"

/** One of a few options, always one chosen: reading order, diff layout. */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string
  value: T
  options: ReadonlyArray<{ value: T; label: ReactNode; title?: string }>
  onChange: (value: T) => void
  className?: string
}) {
  return (
    <ToggleGroup
      aria-label={label}
      value={[value]}
      onValueChange={(next) => {
        const chosen = options.find((option) => next.includes(option.value))
        if (chosen) onChange(chosen.value)
      }}
      spacing={0}
      size="sm"
      variant="outline"
      className={className}
    >
      {options.map((option) => (
        <ToggleGroupItem
          key={option.value}
          value={option.value}
          title={option.title}
          className={cn(
            "text-[11px] font-normal text-muted-foreground",
            "aria-pressed:text-foreground"
          )}
        >
          {option.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}
