import { useCallback, useState } from "react"
import {
  Bot,
  Check,
  ChevronDown,
  CircleAlert,
  Eye,
  Globe,
  Hammer,
  MessageCircle,
  SquarePen,
  Terminal,
  Wrench,
  X,
  Zap,
} from "lucide-react"
import type { KeyboardEvent, ReactNode } from "react"

import type { WorkEntryIconName, WorkEntryView } from "./workEntry"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { formatHoverTimestamp } from "@/features/agents/lib/messageTimestamps"
import { cn } from "@/lib/utils"

const ICONS: Record<WorkEntryIconName, typeof Bot> = {
  bot: Bot,
  check: Check,
  "circle-alert": CircleAlert,
  eye: Eye,
  globe: Globe,
  hammer: Hammer,
  "message-circle": MessageCircle,
  "square-pen": SquarePen,
  terminal: Terminal,
  wrench: Wrench,
  zap: Zap,
}

function WorkEntryIcon({
  name,
  className,
}: {
  name: WorkEntryIconName
  className: string
}) {
  const Icon = ICONS[name]
  return <Icon className={className} aria-hidden />
}

const stopRowToggle = (event: { stopPropagation: () => void }) =>
  event.stopPropagation()

function StatusIndicator({ status }: { status: WorkEntryView["status"] }) {
  if (status === "error") {
    return (
      <Tooltip>
        <TooltipTrigger
          render={
            <span
              className="flex size-4 items-center justify-center"
              aria-label="Tool call failed"
            />
          }
        >
          <X className="block size-3 shrink-0 text-destructive" aria-hidden />
        </TooltipTrigger>
        <TooltipPopup>Failed</TooltipPopup>
      </Tooltip>
    )
  }

  if (status === "completed") {
    return (
      <Tooltip>
        <TooltipTrigger
          render={<span className="flex size-4 items-center justify-center" />}
        >
          <Check className="block size-3 shrink-0 stroke-current" aria-hidden />
        </TooltipTrigger>
        <TooltipPopup>Completed</TooltipPopup>
      </Tooltip>
    )
  }

  return (
    <Tooltip>
      <TooltipTrigger
        render={<span className="flex size-4 items-center justify-center" />}
      >
        <span className="block size-1.5 shrink-0 animate-status-pulse rounded-full bg-current" />
      </TooltipTrigger>
      <TooltipPopup>
        {status === "pending" ? "Waiting" : "Running"}
      </TooltipPopup>
    </Tooltip>
  )
}

/**
 * One line in the agent's work log: icon, heading, dimmed argument, status.
 * Expanding reveals `body` when a tool has a richer renderer (a diff, terminal
 * output) and falls back to the entry's plain text otherwise.
 */
export function WorkEntryRow({
  entry,
  timestamp,
  body,
  trailing,
  onActivate,
  defaultExpanded = false,
}: {
  entry: WorkEntryView
  timestamp?: string
  body?: ReactNode
  trailing?: ReactNode
  /** Clicking the row runs this instead of expanding it (e.g. reveal a file). */
  onActivate?: () => void
  defaultExpanded?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const toggle = useCallback(() => setExpanded((value) => !value), [])

  const canExpand =
    onActivate == null && (body != null || entry.expandedText != null)
  const activate = onActivate ?? (canExpand ? toggle : null)
  const isError = entry.tone === "error"
  const hoverTimestamp = formatHoverTimestamp(timestamp)

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "Enter" && event.key !== " ") return
      event.preventDefault()
      activate?.()
    },
    [activate]
  )

  const rowToggleProps = activate
    ? {
        role: "button" as const,
        tabIndex: 0,
        ...(canExpand ? { "aria-expanded": expanded } : {}),
        "aria-label": entry.preview
          ? `${entry.heading} ${entry.preview}`
          : entry.heading,
        onClick: activate,
        onKeyDown: handleKeyDown,
      }
    : {}

  return (
    <div
      className={cn(
        "group/entry flex flex-col rounded-md px-0.5 py-0.5 transition-colors",
        activate &&
          "cursor-pointer hover:bg-accent/20 focus-visible:ring-2 focus-visible:ring-ring/70 focus-visible:outline-none focus-visible:ring-inset"
      )}
      {...rowToggleProps}
    >
      <div className="flex items-center gap-1.5 select-none">
        <span
          className={cn(
            "flex size-5 shrink-0 items-center justify-center",
            isError
              ? "text-destructive"
              : entry.tone === "thinking"
                ? "text-foreground/92"
                : "text-muted-foreground/65"
          )}
        >
          <WorkEntryIcon
            name={entry.icon}
            className="block size-3.5 shrink-0 stroke-[1.8] opacity-80"
          />
        </span>

        <div className="flex min-w-0 flex-1 items-center gap-1.5">
          <div className="min-w-0 flex-1 overflow-hidden">
            <p className="flex w-full min-w-0 items-baseline gap-1.5 text-[13px] leading-5">
              <span
                className={cn(
                  "min-w-0 shrink truncate font-medium",
                  isError
                    ? "text-destructive"
                    : entry.status === "pending" ||
                        entry.status === "in_progress"
                      ? "shimmer-text"
                      : "text-foreground/82"
                )}
              >
                {entry.heading}
              </span>
              {entry.preview &&
                (entry.previewTooltip ? (
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <span className="min-w-0 flex-1 truncate text-muted-foreground" />
                      }
                    >
                      {entry.preview}
                    </TooltipTrigger>
                    <TooltipPopup className="max-w-md break-all">
                      {entry.previewTooltip}
                    </TooltipPopup>
                  </Tooltip>
                ) : (
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {entry.preview}
                  </span>
                ))}
              {entry.diffStats && (
                <span className="flex shrink-0 items-center gap-1 font-mono text-[11px] text-muted-foreground tabular-nums">
                  <span className="transition-colors group-focus-within/entry:text-success-foreground group-hover/entry:text-success-foreground">
                    +{entry.diffStats.additions}
                  </span>
                  <span aria-hidden>/</span>
                  <span className="transition-colors group-focus-within/entry:text-destructive group-hover/entry:text-destructive">
                    -{entry.diffStats.deletions}
                  </span>
                </span>
              )}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-1 text-muted-foreground">
            {trailing}
            {hoverTimestamp && (
              <time className="text-[10px] tabular-nums opacity-0 transition-opacity group-hover/entry:opacity-100">
                {hoverTimestamp}
              </time>
            )}
            <span
              className="flex size-4 shrink-0 items-center justify-center"
              aria-hidden={!canExpand}
            >
              {canExpand ? (
                <ChevronDown
                  className={cn(
                    "size-3 shrink-0 opacity-70 transition-transform duration-200",
                    expanded && "rotate-180"
                  )}
                  aria-hidden
                />
              ) : null}
            </span>
            <span className="flex size-4 shrink-0 items-center justify-center">
              <StatusIndicator status={entry.status} />
            </span>
          </div>
        </div>
      </div>

      {expanded && canExpand && (
        <div
          className="ms-7 mt-1 cursor-default border-s border-border/45 ps-3 pt-0.5"
          onClick={stopRowToggle}
          onPointerDown={stopRowToggle}
        >
          {body ?? (
            <pre className="max-h-64 cursor-text overflow-auto font-mono text-[12px] leading-relaxed break-words whitespace-pre-wrap text-muted-foreground select-text">
              {entry.expandedText}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
