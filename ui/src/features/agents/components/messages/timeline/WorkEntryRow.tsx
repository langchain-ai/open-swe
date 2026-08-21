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
import { ToolResultBody } from "./ToolResultBody"
import type { ReactNode } from "react"

import type { WorkEntryIconName, WorkEntryView } from "./workEntry"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
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

function WorkEntryLine({
  entry,
  timestamp,
  canExpand,
}: {
  entry: WorkEntryView
  timestamp?: string
  canExpand: boolean
}) {
  const isError = entry.tone === "error"
  const hoverTimestamp = formatHoverTimestamp(timestamp)

  // Spans throughout: this line also renders inside a collapsible's <button>.
  return (
    <span className="flex w-full items-center gap-1.5 select-none">
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

      <span className="flex min-w-0 flex-1 items-center gap-1.5">
        <span className="block min-w-0 flex-1 overflow-hidden">
          <span className="flex w-full min-w-0 items-baseline gap-1.5 text-left text-[13px] leading-5">
            <span
              className={cn(
                "shrink-0 truncate font-medium",
                isError
                  ? "text-destructive"
                  : entry.status === "pending" || entry.status === "in_progress"
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
          </span>
        </span>

        <span className="flex shrink-0 items-center gap-1 text-muted-foreground">
          {hoverTimestamp && (
            <time className="text-[10px] tabular-nums opacity-0 transition-opacity group-hover/entry:opacity-100">
              {hoverTimestamp}
            </time>
          )}
          <span
            className="flex size-4 shrink-0 items-center justify-center"
            aria-hidden={!canExpand}
          >
            {canExpand && (
              <ChevronDown
                className="size-3 shrink-0 opacity-70 transition-transform duration-200 group-data-open/entry:rotate-180"
                aria-hidden
              />
            )}
          </span>
          <span className="flex size-4 shrink-0 items-center justify-center">
            <StatusIndicator status={entry.status} />
          </span>
        </span>
      </span>
    </span>
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
  defaultExpanded = false,
}: {
  entry: WorkEntryView
  timestamp?: string
  body?: ReactNode
  defaultExpanded?: boolean
}) {
  const canExpand = body != null || entry.expandedText != null

  if (!canExpand) {
    return (
      <div className="group/entry flex flex-col rounded-md px-0.5 py-0.5">
        <WorkEntryLine entry={entry} timestamp={timestamp} canExpand={false} />
      </div>
    )
  }

  const label = entry.preview
    ? `${entry.heading} ${entry.preview}`
    : entry.heading

  return (
    <Collapsible
      className="group/entry flex flex-col rounded-md"
      defaultOpen={defaultExpanded}
    >
      <CollapsibleTrigger
        aria-label={label}
        className="w-full cursor-pointer rounded-md px-0.5 py-0.5 transition-colors hover:bg-accent/20 focus-visible:ring-2 focus-visible:ring-ring/70 focus-visible:outline-none focus-visible:ring-inset"
      >
        <WorkEntryLine entry={entry} timestamp={timestamp} canExpand />
      </CollapsibleTrigger>
      <CollapsibleContent className="ms-7 mt-1 cursor-default border-s border-border/45 ps-3 pt-0.5">
        {body ??
          (entry.expandedText != null && (
            <ToolResultBody value={entry.expandedText} />
          ))}
      </CollapsibleContent>
    </Collapsible>
  )
}
