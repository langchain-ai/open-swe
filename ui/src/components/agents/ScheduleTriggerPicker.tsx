import { useEffect, useRef, useState } from "react"
import { CaretRightIcon, ClockIcon, PlusIcon } from "@phosphor-icons/react"

import { CRON_PRESETS } from "@/lib/agents/cron"
import { cn } from "@/lib/utils"

interface ScheduleTriggerPickerProps {
  /** Called with a cron value for a preset, or null when the user picks Custom. */
  onSelect: (cron: string | null) => void
  triggerLabel?: string
}

const OPTIONS: Array<{ id: string; label: string; cron: string | null }> = [
  ...CRON_PRESETS.map((preset) => ({
    id: preset.id,
    label: preset.label,
    cron: preset.value,
  })),
  { id: "custom", label: "Custom (cron)", cron: null },
]

export function ScheduleTriggerPicker({
  onSelect,
  triggerLabel = "Add Trigger",
}: ScheduleTriggerPickerProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-sm text-[var(--ui-text-muted)] transition-colors hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)]"
      >
        <PlusIcon className="size-4" />
        {triggerLabel}
      </button>

      {open && (
        <div className="absolute top-full left-0 z-50 mt-1 w-72 overflow-hidden rounded-xl border border-[var(--ui-border)] bg-[var(--ui-surface)] py-1 shadow-lg">
          <div className="flex items-center gap-2 px-3 py-1.5 text-[11px] font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase">
            <ClockIcon className="size-3.5" />
            Scheduled
          </div>
          {OPTIONS.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => {
                onSelect(option.cron)
                setOpen(false)
              }}
              className={cn(
                "flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-[var(--ui-text-muted)] transition-colors hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)]"
              )}
            >
              <span className="flex-1">{option.label}</span>
              {option.cron === null && (
                <CaretRightIcon className="size-3.5 opacity-50" />
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
