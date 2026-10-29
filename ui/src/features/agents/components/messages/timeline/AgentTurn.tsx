import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { CopyIconButton } from "@langchain/macaw-components/CopyButton"

import { DiffView } from "../../chat/DiffView"
import { ChunkRenderer } from "../ChunkRenderer"
import { ExpandableMessageChip } from "../ExpandableMessageChip"
import { MessageTimestamp } from "../MessageTimestamp"
import { WorkerLaunchMessage } from "../TaskEventMessage"
import { ReasoningBlock } from "../ReasoningBlock"
import {
  buildRenderItems,
  collapsedWorkItems,
  countWorkActions,
  segmentTurn,
} from "../renderItems"
import { WorkEntryRow } from "./WorkEntryRow"
import { describeWorkEntry, hasToolCallDetail, latestDiff } from "./workEntry"
import { TurnFoldRow, WorkGroupToggleRow } from "./foldRows"
import { JsonInputEntryBody, ShellEntryBody, ToolCallBody } from "./entryBodies"
import { ToolResultBody } from "./ToolResultBody"
import type { ReactNode } from "react"
import type { RenderItem, TurnSegment } from "../renderItems"
import type { ApprovalCallbacks } from "../types"
import type { Message, ToolExecutionChunk } from "@/features/agents/lib/types"
import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"
import { OutputIframe } from "@/features/agents/components/chat/OutputIframe"
import {
  ReplyCard,
  replyBody,
} from "@/features/agents/components/chat/ReplyCard"
import { ManagedToolsConnectionCard } from "@/features/agents/components/chat/ManagedToolsConnectionCard"
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
 * How long a reply sits at the end of a live turn before an activity row joins
 * it. A run usually ends right after its answer lands; showing the row at once
 * flashed it under the answer and dropped everything below when it went.
 */
const TRAILING_ACTIVITY_DELAY_MS = 800

/** True once `condition` has held for `delayMs`; false as soon as it stops. */
function useHeld(condition: boolean, delayMs: number): boolean {
  const [held, setHeld] = useState(false)
  useEffect(() => {
    if (!condition) return
    const timer = setTimeout(() => setHeld(true), delayMs)
    return () => {
      clearTimeout(timer)
      setHeld(false)
    }
  }, [condition, delayMs])
  return condition && held
}

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
  if (!diff) return <ToolWorkEntry chunk={chunk} repoPath={repoPath} />
  return (
    <WorkEntryRow
      entry={describeWorkEntry(chunk, repoPath)}
      timestamp={chunk.timestamp}
      body={<DiffView diffData={diff} snippet />}
      defaultExpanded={chunk.status === "pending"}
    />
  )
}

/**
 * A code-mode `eval` call, open on the tool calls its code made, with its code
 * folded above them. Its own output mostly repeats what those returned, so it
 * shows only when the code failed or called nothing.
 */
function CodeWorkEntry({
  chunk,
  calls,
  repoPath,
}: {
  chunk: ToolExecutionChunk
  /** The nested calls, rendered; null when the code called no tools. */
  calls: ReactNode
  repoPath?: string
}) {
  const entry = describeWorkEntry(chunk, repoPath)
  const code =
    typeof chunk.input?.code === "string" ? chunk.input.code.trim() : ""
  if (calls == null) {
    return (
      <WorkEntryRow
        entry={entry}
        timestamp={chunk.timestamp}
        body={
          hasToolCallDetail(chunk, Boolean(code))
            ? ({ loadedText, loadError }) => (
                <ToolCallBody
                  chunk={chunk}
                  loadedText={loadedText}
                  loadError={loadError}
                  input={
                    code && <CodeBlock text={code} language="javascript" />
                  }
                />
              )
            : undefined
        }
      />
    )
  }
  const failed = chunk.status === "error"
  // Keyed apart from the plain row: calls arrive after the row first renders,
  // and only a fresh row opens by default.
  return (
    <WorkEntryRow
      key="calls"
      entry={failed ? entry : { ...entry, loadExpandedText: null }}
      timestamp={chunk.timestamp}
      body={({ loadedText, loadError }) => (
        <div className="space-y-space-2">
          {code && (
            <ExpandableMessageChip label="Code">
              <CodeBlock text={code} language="javascript" />
            </ExpandableMessageChip>
          )}
          <div>{calls}</div>
          {failed && (
            <ToolResultBody
              value={loadError ?? loadedText ?? entry.expandedText ?? ""}
            />
          )}
        </div>
      )}
      defaultExpanded
    />
  )
}

/** A tool call shown as its arguments and output. */
function ToolWorkEntry({
  chunk,
  repoPath,
}: {
  chunk: ToolExecutionChunk
  repoPath?: string
}) {
  const entry = describeWorkEntry(chunk, repoPath)
  return (
    <WorkEntryRow
      entry={entry}
      timestamp={chunk.timestamp}
      body={
        hasToolCallDetail(chunk, Object.keys(chunk.input ?? {}).length > 0)
          ? ({ loadedText, loadError }) => (
              <JsonInputEntryBody
                chunk={chunk}
                loadedText={loadedText}
                loadError={loadError}
              />
            )
          : undefined
      }
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
        <ToolWorkEntry
          key={chunk.toolCallId || `work-${index}`}
          chunk={chunk}
          repoPath={repoPath}
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
  costUsd,
  ...callbacks
}: {
  message: Message
  isStreaming?: boolean
  isMarkdownLive?: boolean
  repoPath?: string
  activityLabel?: string
  costUsd?: number
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

  const settledSegments = useMemo(() => segmentTurn(renderItems), [renderItems])
  const endsInReply = settledSegments.at(-1)?.type === "reply"
  const replyHeld = useHeld(
    !!isStreaming && endsInReply,
    TRAILING_ACTIVITY_DELAY_MS
  )
  const segments = useMemo(
    () =>
      isStreaming && (settledSegments.length === 0 || replyHeld)
        ? [
            ...settledSegments,
            { type: "work" as const, key: "work-live", items: [] },
          ]
        : settledSegments,
    [isStreaming, replyHeld, settledSegments]
  )
  const workSegmentCount = segments.filter((s) => s.type === "work").length
  const liveItemKey = isStreaming ? renderItems.at(-1)?.key : undefined

  const [expandedFolds, setExpandedFolds] = useState<Record<string, boolean>>(
    {}
  )
  const toggleFold = useCallback((key: string) => {
    setExpandedFolds((prev) => ({ ...prev, [key]: !(prev[key] ?? false) }))
  }, [])

  // The answer is what follows the last stretch of work; earlier replies are
  // commentary the agent wrote along the way.
  const replyText = useMemo(
    () =>
      segments
        .slice(segments.findLastIndex((s) => s.type === "work") + 1)
        .map((segment) => {
          if (segment.type !== "reply") return ""
          const { item } = segment
          if (item.type === "reply-item") return `${replyBody(item.chunk)}\n\n`
          return item.type === "text-chunk" && item.chunk.kind === "text"
            ? item.chunk.text
            : ""
        })
        .join("")
        .trim(),
    [segments]
  )

  const turnStart = message.timestampIsFallback ? undefined : message.startedAt
  const turnEnd = message.timestampIsFallback ? undefined : message.timestamp
  const segmentDurationMs = (index: number): number | null => {
    if (workSegmentCount === 1 && measuredDurationMs !== null) {
      return measuredDurationMs
    }
    return elapsedMs(
      replyTimestamp(segments[index - 1]) ?? turnStart,
      replyTimestamp(segments[index + 1]) ?? turnEnd
    )
  }

  const renderItem = (item: RenderItem): ReactNode => {
    switch (item.type) {
      case "reasoning-item": {
        const reasoningChunk =
          item.chunk.kind === "reasoning" ? item.chunk : null
        return (
          <div key={item.key} className="min-w-0 flex-1">
            <ReasoningBlock
              text={reasoningChunk?.text ?? ""}
              isLive={item.key === liveItemKey}
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

      case "code-item": {
        const { calls } = item.chunk
        return (
          <CodeWorkEntry
            key={item.key}
            chunk={item.chunk}
            calls={calls ? buildRenderItems(calls).map(renderItem) : null}
            repoPath={repoPath}
          />
        )
      }

      case "worker-launch-item":
        return <WorkerLaunchMessage key={item.key} chunk={item.chunk} />

      case "reply-item":
        return <ReplyCard key={item.key} chunk={item.chunk} />

      case "managed-tools-item":
        return (
          <ManagedToolsConnectionCard
            key={item.key}
            cardId={item.chunk.toolCallId}
            offer={item.offer}
          />
        )

      case "iframe-item":
        return item.chunk.display ? (
          <OutputIframe key={item.key} display={item.chunk.display} />
        ) : null

      case "sql-item":
        return <SqlResultItem key={item.key} chunk={item.chunk} />

      case "tool-item":
        return (
          <ToolWorkEntry
            key={item.key}
            chunk={item.chunk}
            repoPath={repoPath}
          />
        )

      // Not only prose: buildRenderItems funnels code/error/list/image chunks
      // here too, so this has to go through the full chunk renderer.
      case "text-chunk":
        return (
          <div key={item.key} className="min-w-0 px-space-1 py-0.5">
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

  const renderWorkSegment = (
    segment: Extract<TurnSegment, { type: "work" }>,
    index: number
  ): ReactNode => {
    const live = !!isStreaming && index === segments.length - 1
    const expanded = expandedFolds[segment.key] ?? false
    const durationMs = live ? null : segmentDurationMs(index)
    const label = live
      ? (activityLabel ?? "Working…")
      : durationMs && durationMs >= 1000
        ? `Worked for ${formatElapsed(durationMs)}`
        : "Worked"
    const actionCount = countWorkActions(segment.items)
    const visible = expanded
      ? segment.items
      : collapsedWorkItems(segment.items, !isStreaming)

    return (
      <Fragment key={segment.key}>
        <TurnFoldRow
          label={
            actionCount > 0
              ? `${label} · ${actionCount} action${actionCount === 1 ? "" : "s"}`
              : label
          }
          active={live}
          expanded={expanded}
          onToggle={() => toggleFold(segment.key)}
        />
        {visible.map(renderItem)}
      </Fragment>
    )
  }

  return (
    <div className="group/turn my-space-2 min-w-0 space-y-space-2">
      {segments.map((segment, index) =>
        segment.type === "reply"
          ? renderItem(segment.item)
          : renderWorkSegment(segment, index)
      )}

      {/* As tall as the copy button, which only arrives once the run ends. */}
      <div className="mt-space-1 flex min-h-5 items-center gap-space-1">
        {replyText && !isStreaming && (
          <CopyIconButton
            copy={replyText}
            label="Copy message"
            size="xs"
            className="opacity-0 transition-opacity duration-normal group-hover/turn:opacity-100 focus-visible:opacity-100"
          />
        )}
        {!message.timestampIsFallback && (
          <MessageTimestamp
            timestamp={message.timestamp}
            startedAt={message.startedAt}
            costUsd={costUsd}
          />
        )}
      </div>
    </div>
  )
}

function replyTimestamp(segment?: TurnSegment): string | undefined {
  if (segment?.type !== "reply") return undefined
  const { item } = segment
  if (!("chunk" in item)) return undefined
  const { chunk } = item
  return chunk.kind === "tool-execution" || chunk.kind === "text"
    ? chunk.timestamp
    : undefined
}

function elapsedMs(start?: string, end?: string): number | null {
  if (!start || !end) return null
  const delta = Date.parse(end) - Date.parse(start)
  return Number.isFinite(delta) && delta > 0 ? delta : null
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

  const query =
    typeof chunk.input?.query === "string" ? chunk.input.query.trim() : ""

  return (
    <div>
      {query && <CodeBlock text={query} language="sql" />}
      <SqlResultTable
        output={loaded && loaded.load === loadOutput ? loaded.text : output}
      />
    </div>
  )
}
