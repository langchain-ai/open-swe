import type { ReactNode } from "react"

import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ProviderLogo } from "@langchain/gtm-platform-design-system/ui/provider-logos"

import { ChevronRight, Clock, GitHub } from "@/components/glyphs"
import { AUTOMATION_EVENT_PROVIDERS } from "@/features/agents/lib/types"
import { CRON_PRESETS } from "@/features/automations/lib/cron"

interface TriggerMenuProps {
  /** Lists the schedule presets; called with a cron, or null for Custom. */
  onSchedule?: (cron: string | null) => void
  /** Lists the GitHub provider. */
  onGitHub?: () => void
  /** Lists the Slack provider. */
  onSlack?: () => void
  /** Lists the Linear provider. */
  onLinear?: () => void
  children: ReactNode
  variant?: "outline" | "ghost"
  size?: "compact" | "icon-sm"
  "aria-label"?: string
}

interface ProviderEntry {
  id: string
  mark: ReactNode
  label: string
  item: string
  onSelect: () => void
}

const SCHEDULE_OPTIONS: Array<{
  id: string
  label: string
  cron: string | null
}> = [
  ...CRON_PRESETS.map((preset) => ({
    id: preset.id,
    label: preset.label,
    cron: preset.value,
  })),
  { id: "custom", label: "Custom (cron)", cron: null },
]

export function TriggerMenu({
  onSchedule,
  onGitHub,
  onSlack,
  onLinear,
  children,
  variant = "outline",
  size = "compact",
  "aria-label": ariaLabel,
}: TriggerMenuProps) {
  const providers: Array<ProviderEntry> = []
  if (onGitHub)
    providers.push({
      id: "github",
      mark: <Icon icon={GitHub} size="sm" />,
      label: AUTOMATION_EVENT_PROVIDERS.github.label,
      item: "Issue and pull request events",
      onSelect: onGitHub,
    })
  if (onSlack)
    providers.push({
      id: "slack",
      mark: <ProviderLogo provider="slack" className="size-3.5" />,
      label: AUTOMATION_EVENT_PROVIDERS.slack.label,
      item: "Channel messages",
      onSelect: onSlack,
    })
  if (onLinear)
    providers.push({
      id: "linear",
      mark: <ProviderLogo provider="linear" className="size-3.5" />,
      label: AUTOMATION_EVENT_PROVIDERS.linear.label,
      item: "Issue events",
      onSelect: onLinear,
    })

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant={variant}
            size={size}
            aria-label={ariaLabel}
          />
        }
      >
        {children}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        {providers.map((provider, index) => (
          <DropdownMenuGroup key={provider.id}>
            {index > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="flex items-center gap-2">
              {provider.mark}
              {provider.label}
            </DropdownMenuLabel>
            <DropdownMenuItem onClick={provider.onSelect}>
              <span className="flex-1">{provider.item}</span>
              <Icon icon={ChevronRight} size="sm" className="text-ink-subtle" />
            </DropdownMenuItem>
          </DropdownMenuGroup>
        ))}
        {onSchedule && (
          <DropdownMenuGroup>
            {providers.length > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="flex items-center gap-2">
              <Icon icon={Clock} size="sm" />
              Schedule
            </DropdownMenuLabel>
            {SCHEDULE_OPTIONS.map((option) => (
              <DropdownMenuItem
                key={option.id}
                onClick={() => onSchedule(option.cron)}
              >
                <span className="flex-1">{option.label}</span>
                {option.cron === null && (
                  <Icon
                    icon={ChevronRight}
                    size="sm"
                    className="text-ink-subtle"
                  />
                )}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
