import { useCallback, useEffect, useState } from "react"
import { CaretDownIcon } from "@phosphor-icons/react/dist/ssr/CaretDown"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { CheckIcon } from "@phosphor-icons/react/dist/ssr/Check"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { GlobeIcon } from "@phosphor-icons/react/dist/ssr/Globe"
import { HammerIcon } from "@phosphor-icons/react/dist/ssr/Hammer"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/ssr/PencilSimple"
import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"
import { TerminalIcon } from "@phosphor-icons/react/dist/ssr/Terminal"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"
import { WrenchIcon } from "@phosphor-icons/react/dist/ssr/Wrench"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { Tooltip } from "@langchain/macaw-components/Tooltip"
import { ToolResultBody } from "./ToolResultBody"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import type { KeyboardEvent, ReactNode } from "react"

import type { WorkEntryIconName, WorkEntryView } from "./workEntry"
import { formatHoverTimestamp } from "@/features/agents/lib/messageTimestamps"
import { cn } from "@/lib/utils"

const ICONS: Record<WorkEntryIconName, IconComponent> = {
  bot: RobotIcon,
  check: CheckIcon,
  "circle-alert": WarningCircleIcon,
  eye: EyeIcon,
  globe: GlobeIcon,
  hammer: HammerIcon,
  "message-circle": ChatCircleIcon,
  "square-pen": PencilSimpleIcon,
  terminal: TerminalIcon,
  wrench: WrenchIcon,
  zap: LightningIcon,
}

function WorkEntryIcon({ name }: { name: WorkEntryIconName }) {
  const Glyph = ICONS[name]
  return (
    <Glyph size={14} weight="regular" className="block shrink-0" aria-hidden />
  )
}

const stopRowToggle = (event: { stopPropagation: () => void }) =>
  event.stopPropagation()

function StatusIndicator({ status }: { status: WorkEntryView["status"] }) {
  if (status === "error") {
    return (
      <Tooltip title="Failed">
        <span
          className="flex size-4 items-center justify-center"
          aria-label="Tool call failed"
        >
          <XIcon
            size={12}
            weight="bold"
            className="block shrink-0 text-icon-error"
            aria-hidden
          />
        </span>
      </Tooltip>
    )
  }

  if (status === "completed") {
    return (
      <Tooltip title="Completed">
        <span className="flex size-4 items-center justify-center">
          <CheckIcon
            size={12}
            weight="bold"
            className="block shrink-0"
            aria-hidden
          />
        </span>
      </Tooltip>
    )
  }

  return (
    <Tooltip title={status === "pending" ? "Waiting" : "Running"}>
      <span className="flex size-4 items-center justify-center">
        <span className="block size-1.5 shrink-0 animate-status-pulse rounded-full bg-current" />
      </span>
    </Tooltip>
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
        "group/entry flex flex-col rounded-md px-0.5 py-0.5 transition-colors duration-normal",
        activate &&
          "cursor-pointer hover:bg-surface-level-1-hover focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none focus-visible:ring-inset"
      )}
      {...rowToggleProps}
    >
      <div className="flex items-center gap-1.5 select-none">
        <span
          className={cn(
            "flex size-5 shrink-0 items-center justify-center",
            isError
              ? "text-icon-error"
              : entry.tone === "thinking"
                ? "text-icon-primary"
                : "text-icon-tertiary"
          )}
        >
          <WorkEntryIcon name={entry.icon} />
        </span>

        <div className="flex min-w-0 flex-1 items-center gap-1.5">
          <div className="min-w-0 flex-1 overflow-hidden">
            <p className="flex w-full min-w-0 items-baseline gap-1.5 text-xs leading-5">
              <span
                className={cn(
                  "shrink-0 truncate font-medium",
                  isError
                    ? "text-error-secondary"
                    : entry.status === "pending" ||
                        entry.status === "in_progress"
                      ? "shimmer-text"
                      : "text-primary"
                )}
              >
                {entry.heading}
              </span>
              {entry.preview &&
                (entry.previewTooltip ? (
                  <Tooltip
                    title={entry.previewTooltip}
                    tooltipClassName="max-w-md break-all text-primary"
                  >
                    <span className="min-w-0 flex-1 truncate text-secondary">
                      {entry.preview}
                    </span>
                  </Tooltip>
                ) : (
                  <span className="min-w-0 flex-1 truncate text-secondary">
                    {entry.preview}
                  </span>
                ))}
              {entry.diffStats && (
                <span className="flex shrink-0 items-center gap-1 font-mono text-xxs text-secondary tabular-nums">
                  <span className="transition-colors group-focus-within/entry:text-success-secondary group-hover/entry:text-success-secondary">
                    +{entry.diffStats.additions}
                  </span>
                  <span aria-hidden>/</span>
                  <span className="transition-colors group-focus-within/entry:text-error-secondary group-hover/entry:text-error-secondary">
                    -{entry.diffStats.deletions}
                  </span>
                </span>
              )}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-1 text-secondary">
            {trailing}
            {hoverTimestamp && (
              <time className="text-xxs text-tertiary tabular-nums opacity-0 transition-opacity duration-normal group-hover/entry:opacity-100">
                {hoverTimestamp}
              </time>
            )}
            <span
              className="flex size-4 shrink-0 items-center justify-center"
              aria-hidden={!canExpand}
            >
              {canExpand ? (
                <CaretDownIcon
                  size={12}
                  weight="bold"
                  className={cn(
                    "shrink-0 text-icon-tertiary transition-transform duration-normal",
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
          className="ms-7 mt-1 cursor-default border-s border-subtle ps-3 pt-0.5"
          onClick={stopRowToggle}
          onPointerDown={stopRowToggle}
        >
          {typeof body === "function"
            ? body({ loadedText, loadError })
            : (body ??
              (detailText != null ? (
                <ToolResultBody value={detailText} />
              ) : (
                <p className="text-xxs text-secondary">
                  {loadError ?? "Loading output…"}
                </p>
              )))}
        </div>
      )}
    </div>
  )
}
