import { CaretRightIcon } from "@langchain/macaw-components/icons"
import type { ReactNode } from "react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { KanbanIcon } from "@phosphor-icons/react/dist/ssr/Kanban"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"

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
  className?: string
  "aria-label"?: string
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

function TriggerGroup({
  icon: GroupIcon,
  label,
  children,
}: {
  icon: IconComponent
  label: string
  children: ReactNode
}) {
  return (
    <DropdownMenuGroup>
      <DropdownMenuLabel className="flex items-center gap-space-2 px-space-2 pt-space-2 pb-space-1 text-xxs font-medium text-tertiary">
        <GroupIcon size={14} weight="regular" />
        {label}
      </DropdownMenuLabel>
      {children}
    </DropdownMenuGroup>
  )
}

function TriggerItem({
  label,
  more,
  onSelect,
}: {
  label: string
  more?: boolean
  onSelect: () => void
}) {
  return (
    <DropdownMenuItem
      size="sm"
      onSelect={onSelect}
      className="gap-space-2 text-secondary focus:text-primary"
    >
      <span className="flex-1">{label}</span>
      {more && (
        <CaretRightIcon
          size={14}
          weight="regular"
          className="text-icon-tertiary"
        />
      )}
    </DropdownMenuItem>
  )
}

export function TriggerMenu({
  onSchedule,
  onGitHub,
  onSlack,
  onLinear,
  children,
  className,
  "aria-label": ariaLabel,
}: TriggerMenuProps) {
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button type="button" aria-label={ariaLabel} className={className}>
          {children}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        {onGitHub && (
          <TriggerGroup
            icon={GithubLogoIcon}
            label={AUTOMATION_EVENT_PROVIDERS.github.label}
          >
            <TriggerItem
              label="Issue and pull request events"
              more
              onSelect={onGitHub}
            />
          </TriggerGroup>
        )}
        {onSlack && (
          <TriggerGroup
            icon={SlackLogoIcon}
            label={AUTOMATION_EVENT_PROVIDERS.slack.label}
          >
            <TriggerItem label="Channel messages" more onSelect={onSlack} />
          </TriggerGroup>
        )}
        {onLinear && (
          <TriggerGroup
            icon={KanbanIcon}
            label={AUTOMATION_EVENT_PROVIDERS.linear.label}
          >
            <TriggerItem label="Issue events" more onSelect={onLinear} />
          </TriggerGroup>
        )}
        {onSchedule && (
          <TriggerGroup icon={ClockIcon} label="Schedule">
            {SCHEDULE_OPTIONS.map((option) => (
              <TriggerItem
                key={option.id}
                label={option.label}
                more={option.cron === null}
                onSelect={() => onSchedule(option.cron)}
              />
            ))}
          </TriggerGroup>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
