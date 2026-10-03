import { CaretRightIcon, ClockIcon, PlusIcon } from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import {
  Menu,
  MenuGroup,
  MenuGroupLabel,
  MenuItem,
  MenuPopup,
  MenuTrigger,
} from "@/components/ui/menu"
import { CRON_PRESETS } from "@/features/automations/lib/cron"

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
  return (
    <Menu>
      <MenuTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            className="h-auto w-full justify-start gap-2 rounded-lg px-3 py-2.5 font-normal text-muted-foreground"
          />
        }
      >
        <PlusIcon className="size-4" />
        {triggerLabel}
      </MenuTrigger>
      <MenuPopup align="start" className="w-72">
        <MenuGroup>
          <MenuGroupLabel className="flex items-center gap-2 text-[11px] tracking-wide text-muted-foreground/70 uppercase">
            <ClockIcon className="size-3.5" />
            Scheduled
          </MenuGroupLabel>
          {OPTIONS.map((option) => (
            <MenuItem key={option.id} onClick={() => onSelect(option.cron)}>
              <span className="flex-1">{option.label}</span>
              {option.cron === null && (
                <CaretRightIcon className="opacity-50" />
              )}
            </MenuItem>
          ))}
        </MenuGroup>
      </MenuPopup>
    </Menu>
  )
}
