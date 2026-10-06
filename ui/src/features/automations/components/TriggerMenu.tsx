import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import {
  CaretRightIcon,
  ClockIcon,
  GithubLogoIcon,
  KanbanIcon,
  SlackLogoIcon,
} from "@phosphor-icons/react"

import { AUTOMATION_EVENT_PROVIDERS } from "@/features/agents/lib/types"
import { CRON_PRESETS } from "@/features/automations/lib/cron"
import { cn } from "@/lib/utils"

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

const ITEM =
  "flex w-full items-center gap-2 px-3 py-2 text-left text-meta text-ink-subtle transition-colors hover:bg-hover hover:text-ink"

function GroupLabel({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-2 px-3 pt-2 pb-1 text-meta font-medium tracking-wide text-ink-subtle/70 uppercase">
      {children}
    </div>
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

  const choose = (action: () => void) => {
    action()
    setOpen(false)
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={ariaLabel}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={className}
      >
        {children}
      </button>

      {open && (
        <div className="absolute top-full left-0 z-50 mt-1 w-72 overflow-hidden rounded-control border border-line bg-panel pb-1 shadow-popup">
          {onGitHub && (
            <>
              <GroupLabel>
                <GithubLogoIcon className="size-3.5" />
                {AUTOMATION_EVENT_PROVIDERS.github.label}
              </GroupLabel>
              <button
                type="button"
                onClick={() => choose(onGitHub)}
                className={ITEM}
              >
                <span className="flex-1">Issue and pull request events</span>
                <CaretRightIcon className="size-3.5 opacity-50" />
              </button>
            </>
          )}
          {onSlack && (
            <>
              <GroupLabel>
                <SlackLogoIcon className="size-3.5" />
                {AUTOMATION_EVENT_PROVIDERS.slack.label}
              </GroupLabel>
              <button
                type="button"
                onClick={() => choose(onSlack)}
                className={ITEM}
              >
                <span className="flex-1">Channel messages</span>
                <CaretRightIcon className="size-3.5 opacity-50" />
              </button>
            </>
          )}
          {onLinear && (
            <>
              <GroupLabel>
                <KanbanIcon className="size-3.5" />
                {AUTOMATION_EVENT_PROVIDERS.linear.label}
              </GroupLabel>
              <button
                type="button"
                onClick={() => choose(onLinear)}
                className={ITEM}
              >
                <span className="flex-1">Issue events</span>
                <CaretRightIcon className="size-3.5 opacity-50" />
              </button>
            </>
          )}
          {onSchedule && (
            <>
              <GroupLabel>
                <ClockIcon className="size-3.5" />
                Schedule
              </GroupLabel>
              {SCHEDULE_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => choose(() => onSchedule(option.cron))}
                  className={cn(ITEM)}
                >
                  <span className="flex-1">{option.label}</span>
                  {option.cron === null && (
                    <CaretRightIcon className="size-3.5 opacity-50" />
                  )}
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}
