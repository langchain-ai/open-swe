import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"

import { DiffView } from "../../chat/DiffView"
import { ChunkRenderer } from "../ChunkRenderer"
import { MessageTimestamp } from "../MessageTimestamp"
import { ReasoningBlock } from "../ReasoningBlock"
import {
  buildRenderItems,
  countWorkActions,
  segmentTurn,
  selectCollapsedWorkItems,
} from "../renderItems"
import { MessageCopyButton } from "./MessageCopyButton"
import { WorkEntryRow } from "./WorkEntryRow"
import { describeWorkEntry, latestDiff } from "./workEntry"
import { TurnFoldRow, WorkGroupToggleRow } from "./foldRows"
import { ShellEntryBody } from "./entryBodies"
import type { ReactNode } from "react"
import type { RenderItem } from "../renderItems"
import type { ApprovalCallbacks } from "../types"
import type { Message, ToolExecutionChunk } from "@/features/agents/lib/types"
import { OutputIframe } from "@/features/agents/components/chat/OutputIframe"
import { ReplyCard } from "@/features/agents/components/chat/ReplyCard"
import { SqlResultTable } from "@/features/agents/components/chat/SqlResultTable"
import { SubagentGroup } from "@/features/agents/components/subagents"
import { formatElapsed } from "@/lib/utils"

/**
 * How many entries of a work group stay visible while it is collapsed. One
 * keeps the group's most recent activity legible without letting a long
 * exploration burst push the reply off screen.
 */
const MAX_VISIBLE_WORK_LOG_ENTRIES = 1

/**
 * One row per edit call, showing only what the call targeted. The diff lives in
 * the turn's changed-files card and the side panel, both of which read git —
 * rendering a per-call diff here made repeated edits of one file look duplicated.
 */
function EditWorkEntry({
  chunk,
  repoPath,
}: {
  chunk: ToolExecutionChunk
  repoPath?: string
}) {
  const diff = latestDiff(chunk)
  return (
    <WorkEntryRow
      entry={describeWorkEntry(chunk, repoPath)}
      timestamp={chunk.timestamp}
      body={diff ? <DiffView diffData={diff} snippet /> : undefined}
      defaultExpanded={chunk.status === "pending"}
    />
  )
}

/**
 * A run of related tool calls (exploration, mostly). Collapsed, it shows only
 * the most recent entries plus a toggle for the rest.
 */
function WorkGroup({
  chunks,
  repoPath,
  expanded,
  onToggle,
}: {
  chunks: Array<ToolExecutionChunk>
  repoPath?: string
  expanded: boolean
  onToggle: () => void
}) {
  const hiddenCount = Math.max(0, chunks.length - MAX_VISIBLE_WORK_LOG_ENTRIES)
  const visible = expanded
    ? chunks
    : chunks.slice(chunks.length - MAX_VISIBLE_WORK_LOG_ENTRIES)

  return (
    <div>
      {hiddenCount > 0 && (
        <WorkGroupToggleRow
          hiddenCount={hiddenCount}
          expanded={expanded}
          onToggle={onToggle}
        />
      )}
      {visible.map((chunk, index) => (
        <WorkEntryRow
          key={chunk.toolCallId || `work-${index}`}
          entry={describeWorkEntry(chunk, repoPath)}
          timestamp={chunk.timestamp}
        />
      ))}
    </div>
  )
}

export function AgentTurn({
  message,
  isStreaming,
  isMarkdownLive,
  repoPath,
  activityLabel,
  ...callbacks
}: {
  message: Message
  isStreaming?: boolean
  isMarkdownLive?: boolean
  repoPath?: string
  activityLabel?: string
} & ApprovalCallbacks) {
  const renderItems = useMemo(
    () => buildRenderItems(message.chunks, message.id),
    [message.chunks, message.id]
  )

  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>(
    {}
  )
  const toggleGroup = useCallback((id: string) => {
    setExpandedGroups((prev) => ({ ...prev, [id]: !(prev[id] ?? false) }))
  }, [])

  // Measure wall-clock work time for live runs (most accurate); fall back to
  // the turn's first→last message timestamps for transcripts loaded from state.
  const [measuredDurationMs, setMeasuredDurationMs] = useState<number | null>(
    null
  )
  const workStartRef = useRef<number | null>(null)
  const wasStreamingRef = useRef(false)
  useEffect(() => {
    if (isStreaming) {
      if (workStartRef.current === null) workStartRef.current = Date.now()
      wasStreamingRef.current = true
      return
    }
    if (wasStreamingRef.current && workStartRef.current !== null) {
      setMeasuredDurationMs(Date.now() - workStartRef.current)
      wasStreamingRef.current = false
    }
  }, [isStreaming])

  const workDurationMs = useMemo(() => {
    if (measuredDurationMs !== null) return measuredDurationMs
    if (!message.startedAt || message.timestampIsFallback) return null
    const start = Date.parse(message.startedAt)
    const end = Date.parse(message.timestamp)
    if (!Number.isFinite(start) || !Number.isFinite(end)) return null
    const delta = end - start
    return delta > 0 ? delta : null
  }, [
    measuredDurationMs,
    message.startedAt,
    message.timestamp,
    message.timestampIsFallback,
  ])

  const segments = useMemo(() => segmentTurn(renderItems), [renderItems])
  const lastItemKey = renderItems.at(-1)?.key
  const lastWorkKey = segments.findLast(
    (segment) => segment.kind === "work"
  )?.key
  const replyText = useMemo(() => {
    const trailing: Array<string> = []
    for (let index = segments.length - 1; index >= 0; index -= 1) {
      const segment = segments[index]
      if (!segment || segment.kind !== "message") break
      if (
        segment.item.type === "text-chunk" &&
        segment.item.chunk.kind === "text"
      )
        trailing.unshift(segment.item.chunk.text)
    }
    return trailing.join("").trim()
  }, [segments])
  // While the agent is writing, a live status line still sits beneath it.
  const hasLiveTail = !!isStreaming && segments.at(-1)?.kind !== "work"
  const [expandedSegments, setExpandedSegments] = useState<
    Record<string, boolean>
  >({})
  const toggleSegment = useCallback((key: string) => {
    setExpandedSegments((prev) => ({ ...prev, [key]: !(prev[key] ?? false) }))
  }, [])

  const renderItem = (item: RenderItem): ReactNode => {
    switch (item.type) {
      case "reasoning-item": {
        const reasoningChunk =
          item.chunk.kind === "reasoning" ? item.chunk : null
        return (
          <div key={item.key} className="min-w-0 flex-1">
            <ReasoningBlock
              text={reasoningChunk?.text ?? ""}
              isLive={!!isStreaming && item.key === lastItemKey}
            />
          </div>
        )
      }

      case "explored-group":
        return (
          <WorkGroup
            key={item.key}
            chunks={item.chunks}
            repoPath={repoPath}
            expanded={expandedGroups[item.id] ?? false}
            onToggle={() => toggleGroup(item.id)}
          />
        )

      case "subagent-group":
        return <SubagentGroup key={item.key} chunks={item.chunks} />

      case "edit-item":
        return (
          <EditWorkEntry
            key={item.key}
            chunk={item.chunk}
            repoPath={repoPath}
          />
        )

      case "shell-item":
        return (
          <WorkEntryRow
            key={item.key}
            entry={describeWorkEntry(item.chunk, repoPath)}
            timestamp={item.chunk.timestamp}
            body={({ loadedText, loadError }) => (
              <ShellEntryBody
                chunk={item.chunk}
                loadedText={loadedText}
                loadError={loadError}
              />
            )}
            defaultExpanded={item.chunk.status === "pending"}
          />
        )

      case "reply-item":
        return <ReplyCard key={item.key} chunk={item.chunk} />

      case "iframe-item":
        return item.chunk.display ? (
          <OutputIframe key={item.key} display={item.chunk.display} />
        ) : null

      case "sql-item":
        return <SqlResultItem key={item.key} chunk={item.chunk} />

      case "tool-item":
        return (
          <WorkEntryRow
            key={item.key}
            entry={describeWorkEntry(item.chunk, repoPath)}
            timestamp={item.chunk.timestamp}
          />
        )

      // Not only prose: buildRenderItems funnels code/error/list/image chunks
      // here too, so this has to go through the full chunk renderer.
      case "text-chunk":
        return (
          <div key={item.key} className="min-w-0 px-1 py-0.5">
            <ChunkRenderer
              chunk={item.chunk}
              repoPath={repoPath}
              isMarkdownLive={isMarkdownLive}
              {...callbacks}
            />
          </div>
        )
    }
  }

  const workLabel =
    workDurationMs && workDurationMs >= 1000
      ? `Worked for ${formatElapsed(workDurationMs)}`
      : "Worked"
  const renderWorkSegment = (
    key: string,
    items: Array<RenderItem>,
    isLive: boolean
  ): ReactNode => {
    const expanded = expandedSegments[key] ?? false
    const actionCount = countWorkActions(items)
    const label = isLive
      ? (activityLabel ?? "Working…")
      : key === lastWorkKey
        ? workLabel
        : "Worked"
    const labelWithCount =
      actionCount > 0
        ? `${label} · ${actionCount} action${actionCount === 1 ? "" : "s"}`
        : label
    const shownItems = expanded
      ? items
      : selectCollapsedWorkItems(items, !isStreaming)

    return (
      <Fragment key={key}>
        <TurnFoldRow
          label={labelWithCount}
          active={isLive}
          divider={!isLive && key === lastWorkKey}
          expanded={expanded}
          onToggle={() => toggleSegment(key)}
        />
        {shownItems.map(renderItem)}
      </Fragment>
    )
  }

  return (
    <div className="group/turn my-2 min-w-0 space-y-1.5">
      {segments.map((segment, index) =>
        segment.kind === "message"
          ? renderItem(segment.item)
          : renderWorkSegment(
              segment.key,
              segment.items,
              !!isStreaming && index === segments.length - 1
            )
      )}
      {hasLiveTail && renderWorkSegment("work-live", [], true)}

      <div className="mt-1 flex items-center gap-1">
        {replyText && !isStreaming && (
          <MessageCopyButton
            className="opacity-0 transition-opacity duration-200 group-hover/turn:opacity-100 focus-visible:opacity-100"
            text={replyText}
          />
        )}
        {!message.timestampIsFallback && (
          <MessageTimestamp
            timestamp={message.timestamp}
            startedAt={message.startedAt}
          />
        )}
      </div>
    </div>
  )
}

/**
 * A SQL result table parses the whole output, so a chunk that carries only the
 * transcript's preview has to fetch the rest before it can show anything
 * faithful.
 */
function SqlResultItem({ chunk }: { chunk: ToolExecutionChunk }) {
  const { loadOutput, output } = chunk
  const [loaded, setLoaded] = useState<{
    load: typeof loadOutput
    text: string
  } | null>(null)

  useEffect(() => {
    if (!loadOutput) return
    let active = true
    void loadOutput().then(
      (text) => {
        if (active) setLoaded({ load: loadOutput, text })
      },
      () => {
        // The preview stays up; the row's expand path reports load failures.
      }
    )
    return () => {
      active = false
    }
  }, [loadOutput])

  return (
    <SqlResultTable
      output={loaded && loaded.load === loadOutput ? loaded.text : output}
    />
  )
}
