import { IoCloudOutline, IoLaptopOutline } from "react-icons/io5"
import type { IconType } from "react-icons"

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
      className="relative mb-3 grid grid-cols-2 rounded-lg border border-border/60 bg-sidebar-control-surface p-1"
    >
      <span
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-y-1 left-1 w-[calc(50%-0.25rem)] rounded-md bg-sidebar shadow-sm ring-1 ring-border/70 transition-transform duration-200 ease-out",
          source === "local" && "translate-x-full"
        )}
      />
      <SourceButton
        label="Cloud"
        activity={cloudActivity}
        active={source === "cloud"}
        icon={IoCloudOutline}
        onClick={() => onSourceChange("cloud")}
      />
      <SourceButton
        label="This Mac"
        activity={localActivity}
        active={source === "local"}
        icon={IoLaptopOutline}
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
  icon: IconType
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-label={`${label} threads, ${activity.running} running, ${activity.completed} completed`}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "relative z-10 flex min-w-0 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "text-foreground"
          : "text-muted-foreground hover:text-foreground"
      )}
    >
      <Icon className="size-3.5 shrink-0" />
      <span className="truncate">{label}</span>
      {activity.running > 0 && (
        <ActivityCount
          label={`${activity.running} running`}
          count={activity.running}
          className="bg-primary/15 text-primary"
        />
      )}
      {activity.completed > 0 && (
        <ActivityCount
          label={`${activity.completed} completed`}
          count={activity.completed}
          className="bg-success-foreground/15 text-success-foreground"
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
