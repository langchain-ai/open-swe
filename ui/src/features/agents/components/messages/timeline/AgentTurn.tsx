import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { ChunkRenderer } from "../ChunkRenderer"
import { MessageTimestamp } from "../MessageTimestamp"
import { ReasoningBlock } from "../ReasoningBlock"
import { buildRenderItems } from "../renderItems"
import { TurnChangedFilesCard } from "../TurnChangedFilesCard"
import { MessageCopyButton } from "./MessageCopyButton"
import { WorkEntryRow } from "./WorkEntryRow"
import { describeWorkEntry, latestDiff } from "./workEntry"
import { TurnFoldRow, WorkGroupToggleRow } from "./foldRows"
import { ShellEntryBody } from "./entryBodies"
import type { ReactNode } from "react"
import type { RenderItem } from "../renderItems"
import type { ApprovalCallbacks } from "../types"
import type { Message, ToolExecutionChunk } from "@/features/agents/lib/types"
import { ReplyCard } from "@/features/agents/components/chat/ReplyCard"
import { SubagentGroup } from "@/features/agents/components/subagents"
import { formatElapsed } from "@/lib/utils"

/**
 * How many entries of a work group stay visible while it is collapsed. One
 * keeps the group's most recent activity legible without letting a long
 * exploration burst push the reply off screen.
 */
const MAX_VISIBLE_WORK_LOG_ENTRIES = 1

/**
 * Render-item types that count as the agent's reply rather than its work.
 * Everything before the trailing run of these folds away when a turn settles.
 */
const REPLY_ITEM_TYPES = new Set<RenderItem["type"]>([
  "text-chunk",
  "reply-item",
])

function splitWorkAndReply(items: Array<RenderItem>): {
  workItems: Array<RenderItem>
  replyItems: Array<RenderItem>
} {
  let splitIndex = items.length
  while (splitIndex > 0) {
    const prev = items[splitIndex - 1]
    if (!prev || !REPLY_ITEM_TYPES.has(prev.type)) break
    splitIndex -= 1
  }
  return {
    workItems: items.slice(0, splitIndex),
    replyItems: items.slice(splitIndex),
  }
}

/**
 * One row per edit call, showing only what the call targeted. The diff lives in
 * the turn's changed-files card and the side panel, both of which read git —
 * rendering a per-call diff here made repeated edits of one file look duplicated.
 */
function EditWorkEntry({
  chunk,
  projectPath,
  onOpenFile,
}: {
  chunk: ToolExecutionChunk
  projectPath?: string
  onOpenFile?: (filePath: string) => void
}) {
  const filePath = latestDiff(chunk)?.filePath
  return (
    <WorkEntryRow
      entry={describeWorkEntry(chunk, projectPath)}
      timestamp={chunk.timestamp}
      onActivate={
        filePath && onOpenFile ? () => onOpenFile(filePath) : undefined
      }
    />
  )
}

/**
 * A run of related tool calls (exploration, mostly). Collapsed, it shows only
 * the most recent entries plus a toggle for the rest.
 */
function WorkGroup({
  chunks,
  projectPath,
  expanded,
  onToggle,
}: {
  chunks: Array<ToolExecutionChunk>
  projectPath?: string
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
          entry={describeWorkEntry(chunk, projectPath)}
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
  projectPath,
  threadId,
  isLatestTurn,
  ...callbacks
}: {
  message: Message
  isStreaming?: boolean
  isMarkdownLive?: boolean
  projectPath?: string
  /** Cloud threads only; enables the git-sourced changed-files card. */
  threadId?: string
  isLatestTurn?: boolean
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

  const { workItems, replyItems } = useMemo(
    () => splitWorkAndReply(renderItems),
    [renderItems]
  )
  const replyText = useMemo(
    () =>
      replyItems
        .map((item) =>
          item.type === "text-chunk" && item.chunk.kind === "text"
            ? item.chunk.text
            : ""
        )
        .join("")
        .trim(),
    [replyItems]
  )
  const canFoldWork = !isStreaming && workItems.length > 0
  const [workFoldExpanded, setWorkFoldExpanded] = useState(false)
  const toggleWorkFold = useCallback(
    () => setWorkFoldExpanded((value) => !value),
    []
  )

  const renderItem = (
    item: RenderItem,
    index: number,
    total: number
  ): ReactNode => {
    switch (item.type) {
      case "reasoning-item": {
        const reasoningChunk =
          item.chunk.kind === "reasoning" ? item.chunk : null
        return (
          <div key={item.key} className="min-w-0 flex-1">
            <ReasoningBlock
              text={reasoningChunk?.text ?? ""}
              isLive={!!isStreaming && index === total - 1}
            />
          </div>
        )
      }

      case "explored-group":
        return (
          <WorkGroup
            key={item.key}
            chunks={item.chunks}
            projectPath={projectPath}
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
            projectPath={projectPath}
            onOpenFile={callbacks.onOpenFile}
          />
        )

      case "shell-item":
        return (
          <WorkEntryRow
            key={item.key}
            entry={describeWorkEntry(item.chunk, projectPath)}
            timestamp={item.chunk.timestamp}
            body={<ShellEntryBody chunk={item.chunk} />}
          />
        )

      case "reply-item":
        return <ReplyCard key={item.key} chunk={item.chunk} />

      case "tool-item":
        return (
          <WorkEntryRow
            key={item.key}
            entry={describeWorkEntry(item.chunk, projectPath)}
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
              projectPath={projectPath}
              isMarkdownLive={isMarkdownLive}
              {...callbacks}
            />
          </div>
        )
    }
  }

  const foldLabel =
    workDurationMs && workDurationMs >= 1000
      ? `Worked for ${formatElapsed(workDurationMs)}`
      : "Worked"

  return (
    <div className="group/turn my-2 min-w-0 space-y-1.5">
      {canFoldWork ? (
        <>
          <TurnFoldRow
            label={foldLabel}
            expanded={workFoldExpanded}
            onToggle={toggleWorkFold}
          />
          {workFoldExpanded && (
            <div className="space-y-0.5">
              {workItems.map((item, index) =>
                renderItem(item, index, workItems.length)
              )}
            </div>
          )}
          {replyItems.map((item, index) =>
            renderItem(item, workItems.length + index, renderItems.length)
          )}
        </>
      ) : (
        renderItems.map((item, index) =>
          renderItem(item, index, renderItems.length)
        )
      )}

      {threadId && message.turnKey && !isStreaming && (
        <TurnChangedFilesCard
          threadId={threadId}
          turnKey={message.turnKey}
          isLatestTurn={!!isLatestTurn}
          onOpenFile={callbacks.onOpenFile}
        />
      )}

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
