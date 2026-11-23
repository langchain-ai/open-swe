import { Button } from "@langchain/macaw-components/Button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { CaretDownIcon, CheckIcon } from "@langchain/macaw-components/icons"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { KanbanIcon } from "@phosphor-icons/react/dist/ssr/Kanban"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"
import { useState } from "react"

import { DropdownMenuItem } from "@/components/MenuItem"
import {
  DAY_NAMES,
  DEFAULT_SCHEDULE,
  FREQUENCY_LABELS,
  ScheduleSpec,
  describeCron,
  formatClock,
  localTimeZone,
  ordinal,
  sameTimeZone,
  timeZoneLabel,
} from "@/features/automations/lib/cron"
import type { ScheduleFrequency } from "@/features/automations/lib/cron"
import type { TriggerDraft } from "@/features/automations/lib/triggers"

type TriggerKind = TriggerDraft["kind"]
type ScheduleDraft = Extract<TriggerDraft, { kind: "schedule" }>

const TRIGGER_KINDS: Array<{
  kind: TriggerKind
  icon: IconComponent
  label: string
  description: string
}> = [
  {
    kind: "schedule",
    icon: ClockIcon,
    label: "Schedule",
    description: "Run on a schedule",
  },
  {
    kind: "github",
    icon: GithubLogoIcon,
    label: "GitHub event",
    description: "Run when an issue or pull request changes",
  },
  {
    kind: "slack",
    icon: SlackLogoIcon,
    label: "Slack message",
    description: "Run when someone posts in a channel",
  },
  {
    kind: "linear",
    icon: KanbanIcon,
    label: "Linear issue",
    description: "Run when an issue is created or changes",
  },
]

/** Picks what starts a run: a schedule, or an event in GitHub, Slack or Linear. */
export function TriggerKindMenu({
  value,
  onChange,
  disabled,
}: {
  value: TriggerKind
  onChange: (kind: TriggerKind) => void
  disabled: boolean
}) {
  const current =
    TRIGGER_KINDS.find((option) => option.kind === value) ?? TRIGGER_KINDS[0]!
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <Button
          color="secondary"
          variant="outlined"
          size="md"
          type="button"
          leftDecorator={current.icon}
          rightDecorator={CaretDownIcon}
          aria-label={`Trigger: ${current.label}`}
        >
          {current.label}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-80">
        {TRIGGER_KINDS.map((option) => (
          <DropdownMenuItem
            key={option.kind}
            onSelect={() => {
              if (option.kind !== value) onChange(option.kind)
            }}
            className="items-start py-space-2"
          >
            <option.icon
              size={16}
              weight="regular"
              className="mt-0.5 shrink-0 text-icon-secondary"
            />
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm text-primary">{option.label}</span>
              <span className="text-xs text-tertiary">
                {option.description}
              </span>
            </span>
            {option.kind === value && (
              <CheckIcon
                size={14}
                weight="bold"
                className="mt-0.5 shrink-0 text-icon-brand"
              />
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

const CUSTOM = "custom"
type FrequencyChoice = ScheduleFrequency | typeof CUSTOM

const FREQUENCY_OPTIONS: Array<{ value: FrequencyChoice; label: string }> = [
  ...(
    Object.entries(FREQUENCY_LABELS) as Array<[ScheduleFrequency, string]>
  ).map(([value, label]) => ({ value, label })),
  { value: CUSTOM, label: "Custom (cron)" },
]

// Monday first, as people read a week.
const WEEKDAY_OPTIONS = [1, 2, 3, 4, 5, 6, 0].map((day) => ({
  value: String(day),
  label: DAY_NAMES[day],
}))

/** Days 1–28 run every month; a later day saved elsewhere stays offered so it isn't changed. */
function monthdayOptions(spec: ScheduleSpec) {
  const days = Array.from({ length: 28 }, (_, index) => index + 1)
  if (spec.monthday > 28) days.push(spec.monthday)
  return days.map((day) => ({ value: String(day), label: `Day ${day}` }))
}

function timeOptions(spec: ScheduleSpec) {
  const times = Array.from({ length: 96 }, (_, index) => ({
    hour: Math.floor(index / 4),
    minute: (index % 4) * 15,
  }))
  if (spec.minute % 15 !== 0) {
    times.push({ hour: spec.hour, minute: spec.minute })
    times.sort((a, b) => a.hour * 60 + a.minute - (b.hour * 60 + b.minute))
  }
  return times.map(({ hour, minute }) => ({
    value: `${hour}:${minute}`,
    label: formatClock(hour, minute),
  }))
}

function minuteOptions(spec: ScheduleSpec) {
  const minutes = [0, 15, 30, 45]
  if (!minutes.includes(spec.minute)) minutes.push(spec.minute)
  return minutes
    .sort((a, b) => a - b)
    .map((minute) => ({
      value: String(minute),
      label: `At :${minute.toString().padStart(2, "0")}`,
    }))
}

/** The viewer's own zone, UTC, and the zone saved elsewhere, so opening the editor never changes it. */
function zoneOptions(value: string) {
  const local = localTimeZone()
  const zones = [value]
  for (const zone of [local, "UTC"]) {
    if (!zones.some((each) => sameTimeZone(each, zone))) zones.push(zone)
  }
  return zones.map((zone) => ({
    value: zone,
    label: timeZoneLabel(zone),
    description: sameTimeZone(zone, local) ? `Your time zone (${zone})` : zone,
  }))
}

/**
 * A schedule as plain choices, such as Weekly · Monday · 9:00 AM, in the viewer's own
 * time zone; a schedule saved in another zone shows that zone so its time isn't misread.
 */
export function ScheduleFields({
  draft,
  onChange,
  disabled,
}: {
  draft: ScheduleDraft
  onChange: (change: Partial<ScheduleDraft>) => void
  disabled: boolean
}) {
  const parsed = ScheduleSpec.parse(draft.cron)
  const spec = draft.custom ? null : parsed
  // An hourly cron has no hour, so the last one picked comes back with Daily.
  const [lastHour, setLastHour] = useState(
    parsed && parsed.frequency !== "hourly"
      ? parsed.hour
      : DEFAULT_SCHEDULE.hour
  )
  // Once shown, the zone stays editable even after the viewer picks their own.
  const [showZone] = useState(
    () => !sameTimeZone(draft.timezone, localTimeZone())
  )
  const setSpec = (next: ScheduleSpec) => {
    if (next.frequency !== "hourly") setLastHour(next.hour)
    onChange({ cron: next.cron, custom: false })
  }
  const pickFrequency = (frequency: ScheduleFrequency) => {
    const base = parsed ?? DEFAULT_SCHEDULE
    setSpec(
      base.with(
        base.frequency === "hourly"
          ? { frequency, hour: lastHour }
          : { frequency }
      )
    )
  }
  return (
    <>
      <Select<FrequencyChoice>
        aria-label="Repeats"
        size="md"
        value={spec?.frequency ?? CUSTOM}
        onChange={(frequency) => {
          if (!frequency) return
          if (frequency === CUSTOM) onChange({ custom: true })
          else pickFrequency(frequency)
        }}
        options={FREQUENCY_OPTIONS}
        disabled={disabled}
        triggerClassName="w-36"
      />
      {spec?.frequency === "weekly" && (
        <Select
          aria-label="Day of the week"
          size="md"
          value={String(spec.weekday)}
          onChange={(day) =>
            day && setSpec(spec.with({ weekday: Number(day) }))
          }
          options={WEEKDAY_OPTIONS}
          disabled={disabled}
          triggerClassName="w-36"
        />
      )}
      {spec?.frequency === "monthly" && (
        <Select
          aria-label="Day of the month"
          size="md"
          value={String(spec.monthday)}
          onChange={(day) =>
            day && setSpec(spec.with({ monthday: Number(day) }))
          }
          options={monthdayOptions(spec)}
          hideSearch={false}
          searchPlaceholder="Day…"
          disabled={disabled}
          triggerClassName="w-28"
        />
      )}
      {spec && spec.frequency === "hourly" && (
        <Select
          aria-label="Minute"
          size="md"
          value={String(spec.minute)}
          onChange={(minute) =>
            minute && setSpec(spec.with({ minute: Number(minute) }))
          }
          options={minuteOptions(spec)}
          disabled={disabled}
          triggerClassName="w-28"
        />
      )}
      {spec && spec.frequency !== "hourly" && (
        <Select
          aria-label="Time"
          size="md"
          value={`${spec.hour}:${spec.minute}`}
          onChange={(time) => {
            if (!time) return
            const [hour, minute] = time.split(":").map(Number)
            setSpec(spec.with({ hour, minute }))
          }}
          options={timeOptions(spec)}
          hideSearch={false}
          searchPlaceholder="Search times…"
          disabled={disabled}
          triggerClassName="w-32"
        />
      )}
      {!spec && (
        <Input
          size="md"
          debounceMs={0}
          value={draft.cron}
          onChange={(cron) => onChange({ cron })}
          disabled={disabled}
          placeholder={DEFAULT_SCHEDULE.cron}
          aria-label="Cron schedule"
          className="w-40"
          inputContainerClassName="font-mono"
        />
      )}
      {showZone && (
        <Select
          aria-label="Time zone"
          size="md"
          value={draft.timezone}
          onChange={(timezone) => timezone && onChange({ timezone })}
          options={zoneOptions(draft.timezone)}
          disabled={disabled}
          triggerClassName="w-28"
        />
      )}
    </>
  )
}

/** A note under a schedule: what custom cron means, or which months a late monthly day skips. */
export function scheduleHint(draft: ScheduleDraft): string | null {
  const spec = draft.custom ? null : ScheduleSpec.parse(draft.cron)
  if (spec) {
    return spec.frequency === "monthly" && spec.monthday > 28
      ? `Skips months that have no ${ordinal(spec.monthday)}.`
      : null
  }
  const described = describeCron(draft.cron)
  return described === draft.cron.trim()
    ? "Five cron fields: minute, hour, day of month, month, day of week."
    : described
}
