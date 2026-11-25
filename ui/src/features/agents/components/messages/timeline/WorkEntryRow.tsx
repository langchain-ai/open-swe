import { BrainIcon } from "@phosphor-icons/react/dist/ssr/Brain"
import {
  CheckIcon,
  GlobeRegularIcon,
  WrenchRegularIcon,
} from "@langchain/macaw-components/icons"
import { useId, useState } from "react"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { HammerIcon } from "@phosphor-icons/react/dist/ssr/Hammer"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/ssr/PencilSimple"
import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"
import { TerminalIcon } from "@phosphor-icons/react/dist/ssr/Terminal"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"
import { Tooltip } from "@langchain/macaw-components/Tooltip"
import { Link } from "@langchain/macaw-components/Link"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { ToolResultBody, useLazyOutput } from "./ToolResultBody"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import type { ReactNode } from "react"

import type { WorkEntryIconName, WorkEntryView } from "./workEntry"
import { formatHoverTimestamp } from "@/features/agents/lib/messageTimestamps"
import {
  ChatToolCall,
  type ChatToolCallStatus,
} from "@langchain/macaw-components/ChatToolCall"
import { ExpandableMessageChip } from "../ExpandableMessageChip"

const ICONS: Record<WorkEntryIconName, IconComponent> = {
  bot: RobotIcon,
  brain: BrainIcon,
  check: CheckIcon,
  "circle-alert": WarningCircleIcon,
  eye: EyeIcon,
  globe: GlobeRegularIcon,
  hammer: HammerIcon,
  "message-circle": ChatCircleIcon,
  "square-pen": PencilSimpleIcon,
  terminal: TerminalIcon,
  wrench: WrenchRegularIcon,
  zap: LightningIcon,
}

const TOOL_STATUS = {
  pending: "pending",
  in_progress: "running",
  completed: "succeeded",
  error: "failed",
} satisfies Record<
  Exclude<WorkEntryView["status"], "unknown">,
  ChatToolCallStatus
>

export interface WorkEntryBodyDetail {
  /** The call's full output, once the lazy fetch returned it. */
  loadedText: string | null
  /** Why that fetch failed, when it did. */
  loadError: string | null
}

export type WorkEntryBody =
  | ReactNode
  | ((detail: WorkEntryBodyDetail) => ReactNode)

export function WorkEntryRow({
  entry,
  timestamp,
  body,
  defaultExpanded = false,
  showStatus = true,
}: {
  entry: WorkEntryView
  timestamp?: string
  body?: WorkEntryBody
  defaultExpanded?: boolean
  showStatus?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const id = useId()
  const loadExpandedText = entry.loadExpandedText
  const { loadedText, loadError } = useLazyOutput(loadExpandedText, expanded)

  const detailText = loadedText ?? entry.expandedText
  const canExpand =
    body != null || detailText != null || loadExpandedText != null
  const hoverTimestamp = formatHoverTimestamp(timestamp)
  const Glyph = ICONS[entry.icon]
  const details = canExpand
    ? typeof body === "function"
      ? body({ loadedText, loadError })
      : (body ??
        (detailText != null ? (
          <ToolResultBody value={detailText} />
        ) : (
          <p className="text-xxs text-secondary">
            {loadError ?? "Loading output…"}
          </p>
        )))
    : null
  const hasMetadata = Boolean(
    hoverTimestamp || entry.diffStats || entry.destinationHref
  )
  const meta = hasMetadata ? (
    <>
      {entry.diffStats && (
        <span className="flex gap-space-1 font-mono text-xxs tabular-nums">
          <span className="text-success-secondary">
            +{entry.diffStats.additions}
          </span>
          <span className="text-error-secondary">
            -{entry.diffStats.deletions}
          </span>
        </span>
      )}
      {hoverTimestamp && (
        <time className="text-xxs text-tertiary tabular-nums opacity-0 transition-opacity duration-normal group-focus-within/entry:opacity-100 group-hover/entry:opacity-100">
          {hoverTimestamp}
        </time>
      )}
      {entry.destinationHref && (
        <Tooltip title={entry.destinationLabel ?? "Open destination thread"}>
          <Link
            href={entry.destinationHref}
            aria-label={entry.destinationLabel ?? "Open destination thread"}
            className="shrink-0 text-icon-secondary"
          >
            <ArrowUpRightIcon size={14} weight="regular" aria-hidden />
          </Link>
        </Tooltip>
      )}
    </>
  ) : null

  if (entry.status === "unknown" || !showStatus) {
    const label = (
      <span className="inline-flex items-center gap-space-2">
        <Glyph size={12} weight="regular" aria-hidden />
        {entry.heading}
        {entry.preview && (
          <span className="text-secondary">{entry.preview}</span>
        )}
      </span>
    )
    return (
      <div className="group/entry">
        {details ? (
          <ExpandableMessageChip
            label={label}
            actions={meta}
            expanded={expanded}
            onExpandedChange={setExpanded}
            accessibleLabel={
              entry.status === "unknown"
                ? [entry.heading, entry.preview].filter(Boolean).join(" ")
                : `Details for ${entry.heading}`
            }
          >
            {details}
          </ExpandableMessageChip>
        ) : (
          <div className="flex items-center gap-space-2 text-xs">
            {label}
            {meta}
          </div>
        )}
      </div>
    )
  }

  return (
    <div title={entry.heading} className="min-w-0 flex-1">
      <ChatToolCall
        toolCall={{
          id,
          name: entry.heading,
          status: TOOL_STATUS[entry.status],
        }}
        icon={<Glyph size={12} weight="regular" aria-hidden />}
        summary={
          entry.previewTooltip ? (
            <Tooltip title={entry.previewTooltip}>
              <span>{entry.preview}</span>
            </Tooltip>
          ) : (
            entry.preview
          )
        }
        meta={meta}
        expanded={expanded}
        onExpandedChange={setExpanded}
        className="group/entry min-w-0 flex-1"
      >
        {details}
      </ChatToolCall>
    </div>
  )
}
