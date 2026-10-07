import { useCallback, useEffect, useState } from "react"
import { ToolResultBody } from "./ToolResultBody"
import type { KeyboardEvent, ReactNode } from "react"

import type { WorkEntryIconName, WorkEntryView } from "./workEntry"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@langchain/gtm-platform-design-system/ui/tooltip"
import {
  AlertCircle,
  AlertTriangle,
  Bot,
  Check,
  ChevronRight,
  Clock,
  Eye,
  Globe,
  Hammer,
  MessageCircle,
  Pencil,
  Terminal,
  Wrench,
  Zap,
} from "@/components/glyphs"
import type { Glyph } from "@/components/glyphs"
import { formatHoverTimestamp } from "@/features/agents/lib/messageTimestamps"
import { cn } from "@/lib/utils"

const GLYPHS: Record<WorkEntryIconName, Glyph> = {
  bot: Bot,
  check: Check,
  "circle-alert": AlertCircle,
  eye: Eye,
  globe: Globe,
  hammer: Hammer,
  "message-circle": MessageCircle,
  "square-pen": Pencil,
  terminal: Terminal,
  wrench: Wrench,
  zap: Zap,
}

const stopRowToggle = (event: { stopPropagation: () => void }) =>
  event.stopPropagation()

/**
 * The row's mark, in one 20px slot so the heading never shifts as a call
 * resolves: the tool's glyph once settled, a spinner while it runs, a clock
 * while it waits on approval, and the alert when it failed.
 */
function EntryMark({ entry }: { entry: WorkEntryView }) {
  if (entry.status === "error") {
    return (
      <Icon
        icon={AlertTriangle}
        size="sm"
        label="Tool call failed"
        className="text-risk"
      />
    )
  }
  if (entry.status === "in_progress") {
    return <Spinner size="sm" label="Running" className="text-ink-subtle" />
  }
  if (entry.status === "pending") {
    return (
      <Icon icon={Clock} size="sm" label="Waiting" className="text-attention" />
    )
  }
  return (
    <Icon
      icon={GLYPHS[entry.icon]}
      size="sm"
      className={entry.tone === "thinking" ? "text-ink" : "text-ink-subtle"}
    />
  )
}

/** What the row learned by expanding, for a body that renders output itself. */
export interface WorkEntryBodyDetail {
  /** The call's full output, once the lazy fetch returned it. */
  loadedText: string | null
  /** Why that fetch failed, when it did. */
  loadError: string | null
}

/**
 * A custom expanded body: either fixed content, or a function that also gets
 * the output fetched on expand — a body that renders the output itself has to
 * be handed it, or the row's own fallback would be the only thing that ever
 * showed more than the snapshot's preview.
 */
export type WorkEntryBody =
  | ReactNode
  | ((detail: WorkEntryBodyDetail) => ReactNode)

/**
 * One line in the agent's work log: mark, heading, dimmed argument. Expanding
 * reveals `body` when a tool has a richer renderer (a diff, terminal output)
 * and falls back to the entry's plain text otherwise.
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
  body?: WorkEntryBody
  trailing?: ReactNode
  /** Clicking the row runs this instead of expanding it (e.g. reveal a file). */
  onActivate?: () => void
  defaultExpanded?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const toggle = useCallback(() => setExpanded((value) => !value), [])
  const [loadedText, setLoadedText] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const loadExpandedText = entry.loadExpandedText

  // Expanding is what pays for the full output; the preview (if any) shows
  // until it arrives, and a failure says so instead of hanging on a spinner.
  useEffect(() => {
    if (!expanded || !loadExpandedText) return
    if (loadedText !== null || loadError !== null) return
    let active = true
    void loadExpandedText().then(
      (text) => {
        if (active) setLoadedText(text)
      },
      (error: unknown) => {
        if (!active) return
        setLoadError(
          error instanceof Error ? error.message : "Could not load output"
        )
      }
    )
    return () => {
      active = false
    }
  }, [expanded, loadExpandedText, loadError, loadedText])

  const detailText = loadedText ?? entry.expandedText
  const canExpand =
    onActivate == null &&
    (body != null || detailText != null || loadExpandedText != null)
  const activate = onActivate ?? (canExpand ? toggle : null)
  const isError = entry.tone === "error"
  const isLive = entry.status === "pending" || entry.status === "in_progress"
  const hoverTimestamp = formatHoverTimestamp(timestamp)
  const open = expanded && canExpand

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
        "group/entry flex min-w-0 flex-col rounded-compact outline-none",
        activate &&
          "cursor-pointer focus-visible:ring-2 focus-visible:ring-primary"
      )}
      {...rowToggleProps}
    >
      <Inline
        gap="sm"
        align="center"
        className={cn(
          "-mx-1.5 min-h-6 rounded-compact px-1.5 py-0.5 select-none",
          activate &&
            "transition-colors duration-fast ease-out-quint hover:bg-hover motion-reduce:transition-none"
        )}
      >
        <Box
          render={<span />}
          className="flex size-5 shrink-0 items-center justify-center"
        >
          <EntryMark entry={entry} />
        </Box>

        <Box
          render={<p />}
          className="flex min-w-0 flex-1 items-baseline gap-1.5 text-label"
        >
          <Box
            render={<span />}
            className={cn(
              "shrink-0 truncate font-medium",
              isError ? "text-risk" : isLive ? "shimmer-text" : "text-ink"
            )}
          >
            {entry.heading}
          </Box>
          {entry.preview &&
            (entry.previewTooltip ? (
              <Tooltip>
                <TooltipTrigger
                  render={
                    <span className="min-w-0 flex-1 truncate text-ink-subtle" />
                  }
                >
                  {entry.preview}
                </TooltipTrigger>
                <TooltipContent className="max-w-md break-all">
                  {entry.previewTooltip}
                </TooltipContent>
              </Tooltip>
            ) : (
              <Box
                render={<span />}
                className="min-w-0 flex-1 truncate text-ink-subtle"
              >
                {entry.preview}
              </Box>
            ))}
          {entry.diffStats && (
            <Inline
              render={<span />}
              gap="xs"
              align="center"
              className="shrink-0 font-mono text-meta text-ink-subtle tabular-nums"
            >
              <span className="transition-colors duration-fast ease-out-quint group-focus-within/entry:text-positive group-hover/entry:text-positive motion-reduce:transition-none">
                +{entry.diffStats.additions}
              </span>
              <span aria-hidden>/</span>
              <span className="transition-colors duration-fast ease-out-quint group-focus-within/entry:text-risk group-hover/entry:text-risk motion-reduce:transition-none">
                -{entry.diffStats.deletions}
              </span>
            </Inline>
          )}
        </Box>

        <Inline gap="xs" align="center" className="shrink-0 text-ink-subtle">
          {trailing}
          {hoverTimestamp && (
            <time className="text-meta tabular-nums opacity-0 transition-opacity duration-fast ease-out-quint group-hover/entry:opacity-100 motion-reduce:transition-none">
              {hoverTimestamp}
            </time>
          )}
          {canExpand && (
            <Icon
              icon={ChevronRight}
              size="sm"
              className={cn(
                "transition-[opacity,rotate] duration-fast ease-out-quint group-hover/entry:opacity-100 group-focus-visible/entry:opacity-100 motion-reduce:transition-none",
                open ? "rotate-90 opacity-100" : "opacity-0"
              )}
            />
          )}
        </Inline>
      </Inline>

      {open && (
        <div
          className="mt-1 mb-1.5 ml-2.5 cursor-default border-l border-line pl-4"
          onClick={stopRowToggle}
          onPointerDown={stopRowToggle}
        >
          {typeof body === "function"
            ? body({ loadedText, loadError })
            : (body ??
              (detailText != null ? (
                <ToolResultBody value={detailText} />
              ) : loadError ? (
                <Box render={<p />} className="text-meta text-risk">
                  {loadError}
                </Box>
              ) : (
                <Inline
                  gap="xs"
                  align="center"
                  className="text-meta text-ink-subtle"
                >
                  <Spinner size="sm" />
                  Loading output…
                </Inline>
              )))}
        </div>
      )}
    </div>
  )
}
