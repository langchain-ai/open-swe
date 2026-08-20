import { CloudIcon, LaptopIcon } from "@phosphor-icons/react"

import type { DesktopThreadSource } from "@/features/agents/lib/desktopThreadSource"
import { cn } from "@/lib/utils"

export interface ThreadActivity {
  running: number
  completed: number
}

export function DesktopThreadSourceToggle({
  source,
  localActivity,
  cloudActivity,
  onSourceChange,
}: {
  source: DesktopThreadSource
  localActivity: ThreadActivity
  cloudActivity: ThreadActivity
  onSourceChange: (source: DesktopThreadSource) => void
}) {
  return (
    <div
      role="group"
      aria-label="Thread location"
      className="mb-3 grid grid-cols-2 gap-0.5 rounded-lg bg-sidebar-control-surface p-0.5"
    >
      <SourceButton
        label="Cloud"
        activity={cloudActivity}
        active={source === "cloud"}
        icon={CloudIcon}
        onClick={() => onSourceChange("cloud")}
      />
      <SourceButton
        label="This Mac"
        activity={localActivity}
        active={source === "local"}
        icon={LaptopIcon}
        onClick={() => onSourceChange("local")}
      />
    </div>
  )
}

function SourceButton({
  label,
  activity,
  active,
  icon: Icon,
  onClick,
}: {
  label: string
  activity: ThreadActivity
  active: boolean
  icon: typeof CloudIcon
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-label={`${label} threads, ${activity.running} running, ${activity.completed} completed`}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "flex min-w-0 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] font-medium transition-colors",
        active
          ? "bg-sidebar text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground"
      )}
    >
      <Icon className="size-3.5 shrink-0" />
      <span className="truncate">{label}</span>
      {activity.running > 0 && (
        <ActivityCount
          label={`${activity.running} running`}
          count={activity.running}
          className="bg-primary text-primary-foreground"
        />
      )}
      {activity.completed > 0 && (
        <ActivityCount
          label={`${activity.completed} completed`}
          count={activity.completed}
          className="bg-success-foreground text-background"
        />
      )}
    </button>
  )
}

function ActivityCount({
  label,
  count,
  className,
}: {
  label: string
  count: number
  className: string
}) {
  return (
    <span
      className={cn(
        "inline-flex min-w-4 items-center justify-center rounded-full px-1 text-[9px] leading-4 font-semibold tabular-nums",
        className
      )}
      title={label}
    >
      {count}
    </span>
  )
}
