import { CloudIcon, LaptopIcon } from "@phosphor-icons/react"

import type { DesktopThreadSource } from "@/features/agents/lib/desktopThreadSource"
import { cn } from "@/lib/utils"

export function DesktopThreadSourceToggle({
  source,
  localCount,
  cloudCount,
  onSourceChange,
}: {
  source: DesktopThreadSource
  localCount: number
  cloudCount: number
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
        count={cloudCount}
        active={source === "cloud"}
        icon={CloudIcon}
        onClick={() => onSourceChange("cloud")}
      />
      <SourceButton
        label="This Mac"
        count={localCount}
        active={source === "local"}
        icon={LaptopIcon}
        onClick={() => onSourceChange("local")}
      />
    </div>
  )
}

function SourceButton({
  label,
  count,
  active,
  icon: Icon,
  onClick,
}: {
  label: string
  count: number
  active: boolean
  icon: typeof CloudIcon
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-label={`${label} threads, ${count}`}
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
      <span
        className={cn(
          "text-[9px] tabular-nums",
          active ? "text-muted-foreground" : "text-muted-foreground/70"
        )}
      >
        {count}
      </span>
    </button>
  )
}
