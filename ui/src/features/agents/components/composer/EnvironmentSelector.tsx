import { useEffect, useRef, useState } from "react"
import { CaretDownIcon, StackIcon } from "@phosphor-icons/react"

import type { EnvironmentOption } from "@/lib/api"
import { cn } from "@/lib/utils"

interface EnvironmentSelectorProps {
  environments: Array<EnvironmentOption>
  selectedSlug: string | null
  onChange: (slug: string | null) => void
  disabled?: boolean
}

/**
 * Picks the environment a new thread's sandbox boots from.
 *
 * Only rendered when there is more than one to choose between — with a single
 * environment (or none) the choice is already made, so the control would be
 * noise.
 */
export function EnvironmentSelector({
  environments,
  selectedSlug,
  onChange,
  disabled = false,
}: EnvironmentSelectorProps) {
  const [open, setOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      const target = e.target as Node
      if (dropdownRef.current && !dropdownRef.current.contains(target)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  if (environments.length < 2) return null

  const selected = environments.find((env) => env.slug === selectedSlug)

  return (
    <div ref={dropdownRef} className="relative min-w-0 shrink">
      <button
        type="button"
        disabled={disabled}
        aria-label="Environment"
        onClick={() => setOpen((value) => !value)}
        className="flex max-w-[220px] cursor-pointer items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60"
      >
        <StackIcon className="size-3.5 shrink-0" />
        <span className="flex-1 truncate text-left">
          {selected?.name ?? "Default environment"}
        </span>
        <CaretDownIcon className="size-3 shrink-0 opacity-70" />
      </button>
      {open && (
        <div className="absolute top-full left-0 z-50 mt-1 flex max-h-72 w-64 flex-col overflow-y-auto rounded border border-border bg-popover text-xs text-popover-foreground shadow-lg">
          {environments.map((env) => {
            const isSelected = env.slug === selectedSlug
            return (
              <button
                key={env.slug}
                type="button"
                onClick={() => {
                  onChange(env.slug)
                  setOpen(false)
                }}
                className={cn(
                  "flex w-full items-center px-2 py-1.5 text-left transition-colors hover:bg-muted",
                  isSelected ? "text-foreground" : "text-muted-foreground"
                )}
              >
                <span className="truncate">{env.name}</span>
                {!env.has_snapshot && (
                  <span className="ml-2 shrink-0 text-[10px] text-muted-foreground">
                    no snapshot
                  </span>
                )}
                {isSelected && (
                  <span className="ml-auto pl-3 text-muted-foreground">✓</span>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
