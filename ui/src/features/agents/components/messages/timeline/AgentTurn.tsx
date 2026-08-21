import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { DiffView } from "../../chat/DiffView"
import { ChunkRenderer } from "../ChunkRenderer"
import { MessageTimestamp } from "../MessageTimestamp"
import { ReasoningBlock } from "../ReasoningBlock"
import {
  buildRenderItems,
  countWorkActions,
  selectCollapsedTurnItems,
  splitWorkAndReply,
} from "../renderItems"
import { TurnChangedFilesCard } from "../TurnChangedFilesCard"
import { MessageCopyButton } from "./MessageCopyButton"
import { WorkEntryRow } from "./WorkEntryRow"
import { describeWorkEntry, latestDiff } from "./workEntry"
import {
  TurnFoldRow,
  WorkGroupToggleRow,
  workGroupToggleLabel,
} from "./foldRows"
import { ShellEntryBody } from "./entryBodies"
import type { ReactNode } from "react"
import type { RenderItem } from "../renderItems"
import type { MessagesProps } from "../types"
import type {
  Message as ThreadMessage,
  ToolExecutionChunk,
} from "@/features/agents/lib/types"
import {
  Message,
  MessageActions,
  MessageToolbar,
} from "@/components/ai-elements/message"
import { Task, TaskContent, TaskTrigger } from "@/components/ai-elements/task"
import { OutputIframe } from "@/features/agents/components/chat/OutputIframe"
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
 * One row per edit call, showing only what the call targeted. The diff lives in
 * the turn's changed-files card and the side panel, both of which read git —
 * rendering a per-call diff here made repeated edits of one file look duplicated.
 */
function EditWorkEntry({
  chunk,
  projectPath,
}: {
  chunk: ToolExecutionChunk
  projectPath?: string
}) {
  const diff = latestDiff(chunk)
  return (
    <WorkEntryRow
      entry={describeWorkEntry(chunk, projectPath)}
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
  const earlier = chunks.slice(0, hiddenCount)
  const latest = chunks.slice(hiddenCount)
  const row = (chunk: ToolExecutionChunk, index: number) => (
    <WorkEntryRow
      key={chunk.toolCallId || `work-${index}`}
      entry={describeWorkEntry(chunk, projectPath)}
      timestamp={chunk.timestamp}
    />
  )

  if (hiddenCount === 0) return <div>{latest.map(row)}</div>

  return (
    <Task open={expanded} onOpenChange={onToggle}>
      <TaskTrigger title={workGroupToggleLabel(hiddenCount, expanded)}>
        <WorkGroupToggleRow hiddenCount={hiddenCount} expanded={expanded} />
      </TaskTrigger>
      <TaskContent>{earlier.map(row)}</TaskContent>
      {latest.map(row)}
    </Task>
  )
}

export function AgentTurn({
  message,
  isStreaming,
  isMarkdownLive,
  projectPath,
  threadId,
  isLatestTurn,
  activityLabel,
  onOpenFile,
}: {
  message: ThreadMessage
  isStreaming?: boolean
  isMarkdownLive?: boolean
  projectPath?: string
  /** Cloud threads only; enables the git-sourced changed-files card. */
  threadId?: string
  isLatestTurn?: boolean
  activityLabel?: string
  onOpenFile?: MessagesProps["onOpenFile"]
}) {
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
  const collapsedItems = useMemo(
    () => selectCollapsedTurnItems(renderItems, !isStreaming),
    [isStreaming, renderItems]
  )
  const actionCount = useMemo(() => countWorkActions(workItems), [workItems])
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
  const canFoldWork = !!isStreaming || workItems.length > 0
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
        return (
          <div key={item.key} className="min-w-0 flex-1">
            <ReasoningBlock
              text={item.chunk.text}
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
          />
        )

      case "shell-item":
        return (
          <WorkEntryRow
            key={item.key}
            entry={describeWorkEntry(item.chunk, projectPath)}
            timestamp={item.chunk.timestamp}
            body={<ShellEntryBody chunk={item.chunk} />}
            defaultExpanded={item.chunk.status === "pending"}
          />
        )

      case "reply-item":
        return <ReplyCard key={item.key} chunk={item.chunk} />

      case "iframe-item":
        return item.chunk.display ? (
          <OutputIframe key={item.key} display={item.chunk.display} />
        ) : null

      case "tool-item":
        return (
          <WorkEntryRow
            key={item.key}
            entry={describeWorkEntry(item.chunk, projectPath)}
            timestamp={item.chunk.timestamp}
          />
        )

      // Prose or an inline image.
      case "text-chunk":
        return (
          <div key={item.key} className="min-w-0 px-1 py-0.5">
            <ChunkRenderer chunk={item.chunk} isMarkdownLive={isMarkdownLive} />
          </div>
        )
    }
  }

  const workLabel =
    workDurationMs && workDurationMs >= 1000
      ? `Worked for ${formatElapsed(workDurationMs)}`
      : "Worked"
  const foldLabel = isStreaming ? (activityLabel ?? "Working…") : workLabel
  const foldLabelWithCount =
    actionCount > 0
      ? `${foldLabel} · ${actionCount} action${actionCount === 1 ? "" : "s"}`
      : foldLabel
  const visibleItems =
    canFoldWork && workFoldExpanded
      ? renderItems
      : isStreaming || canFoldWork
        ? collapsedItems
        : renderItems

  return (
    <Message from="assistant" className="my-2 gap-1.5">
      {canFoldWork && (
        <TurnFoldRow
          label={foldLabelWithCount}
          active={!!isStreaming}
          expanded={workFoldExpanded}
          onToggle={toggleWorkFold}
        />
      )}
      {visibleItems.map((item, index) =>
        renderItem(item, index, visibleItems.length)
      )}

      {threadId && message.turnKey && !isStreaming && (
        <TurnChangedFilesCard
          threadId={threadId}
          turnKey={message.turnKey}
          isLatestTurn={!!isLatestTurn}
          onOpenFile={onOpenFile}
        />
      )}

      <MessageToolbar>
        {replyText && !isStreaming && (
          <MessageActions>
            <MessageCopyButton
              className="opacity-0 transition-opacity duration-200 group-hover/message:opacity-100 focus-visible:opacity-100"
              text={replyText}
            />
          </MessageActions>
        )}
        {!message.timestampIsFallback && (
          <MessageTimestamp
            timestamp={message.timestamp}
            startedAt={message.startedAt}
          />
        )}
      </MessageToolbar>
    </Message>
  )
}
