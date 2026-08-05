import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ChunkRenderer } from "../ChunkRenderer";
import { MessageTimestamp } from "../MessageTimestamp";
import { ReasoningBlock } from "../ReasoningBlock";
import { buildRenderItems } from "../renderItems";
import { summarizeChangedFiles } from "../summarizeChangedFiles";
import { TurnChangedFilesCard } from "../TurnChangedFilesCard";
import { MessageCopyButton } from "./MessageCopyButton";
import { WorkEntryRow } from "./WorkEntryRow";
import { describeWorkEntry, latestDiff } from "./workEntry";
import { TurnFoldRow, WorkGroupToggleRow } from "./foldRows";
import { DiffEntryBody, DiffStatChip, ShellEntryBody } from "./entryBodies";
import type { ReactNode } from "react";
import type { RenderItem } from "../renderItems";
import type { ApprovalCallbacks, ChangedFileSummaryItem } from "../types";
import type { Message, ToolExecutionChunk } from "@/features/agents/lib/types";
import { ReplyCard } from "@/features/agents/components/chat/ReplyCard";
import { SubagentGroup } from "@/features/agents/components/subagents";
import { countLineChanges } from "@/features/agents/utils/diffStats";
import { formatElapsed } from "@/lib/utils";

/**
 * How many entries of a work group stay visible while it is collapsed. One
 * keeps the group's most recent activity legible without letting a long
 * exploration burst push the reply off screen.
 */
const MAX_VISIBLE_WORK_LOG_ENTRIES = 1;

/**
 * Render-item types that count as the agent's reply rather than its work.
 * Everything before the trailing run of these folds away when a turn settles.
 */
const REPLY_ITEM_TYPES = new Set<RenderItem["type"]>(["text-chunk", "reply-item"]);

function splitWorkAndReply(items: Array<RenderItem>): {
  workItems: Array<RenderItem>;
  replyItems: Array<RenderItem>;
} {
  let splitIndex = items.length;
  while (splitIndex > 0) {
    const prev = items[splitIndex - 1];
    if (!prev || !REPLY_ITEM_TYPES.has(prev.type)) break;
    splitIndex -= 1;
  }
  return { workItems: items.slice(0, splitIndex), replyItems: items.slice(splitIndex) };
}

function EditWorkEntry({
  chunk,
  projectPath,
  resolved,
}: {
  chunk: ToolExecutionChunk;
  projectPath?: string;
  resolved?: ChangedFileSummaryItem;
}) {
  const entry = describeWorkEntry(chunk, projectPath);
  const diff = latestDiff(chunk);

  const { body, trailing } = useMemo(() => {
    if (!diff) return { body: undefined, trailing: undefined };

    const originalContent = resolved?.originalContent ?? diff.originalContent ?? "";
    const newContent = resolved?.modifiedContent ?? diff.newContent;
    const stats = countLineChanges(originalContent, newContent, diff.filePath);

    return {
      body: (
        <DiffEntryBody
          diffData={diff}
          originalContent={originalContent}
          newContent={newContent}
        />
      ),
      trailing: <DiffStatChip additions={stats.additions} deletions={stats.deletions} />,
    };
  }, [diff, resolved]);

  return (
    <WorkEntryRow entry={entry} timestamp={chunk.timestamp} body={body} trailing={trailing} />
  );
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
  chunks: Array<ToolExecutionChunk>;
  projectPath?: string;
  expanded: boolean;
  onToggle: () => void;
}) {
  const hiddenCount = Math.max(0, chunks.length - MAX_VISIBLE_WORK_LOG_ENTRIES);
  const visible = expanded ? chunks : chunks.slice(chunks.length - MAX_VISIBLE_WORK_LOG_ENTRIES);

  return (
    <div>
      {hiddenCount > 0 && (
        <WorkGroupToggleRow hiddenCount={hiddenCount} expanded={expanded} onToggle={onToggle} />
      )}
      {visible.map((chunk, index) => (
        <WorkEntryRow
          key={chunk.toolCallId || `work-${index}`}
          entry={describeWorkEntry(chunk, projectPath)}
          timestamp={chunk.timestamp}
        />
      ))}
    </div>
  );
}

export function AgentTurn({
  message,
  isStreaming,
  isMarkdownLive,
  projectPath,
  ...callbacks
}: {
  message: Message;
  isStreaming?: boolean;
  isMarkdownLive?: boolean;
  projectPath?: string;
} & ApprovalCallbacks) {
  const renderItems = useMemo(
    () => buildRenderItems(message.chunks, message.id),
    [message.chunks, message.id],
  );
  const changedFiles = useMemo(() => summarizeChangedFiles(message.chunks), [message.chunks]);
  const changedFilesTotals = useMemo(() => {
    let additions = 0;
    let deletions = 0;
    for (const item of changedFiles) {
      additions += item.additions;
      deletions += item.deletions;
    }
    return { additions, deletions };
  }, [changedFiles]);
  const changedFilesByPath = useMemo(() => {
    const byPath = new Map<string, ChangedFileSummaryItem>();
    for (const file of changedFiles) byPath.set(file.filePath, file);
    return byPath;
  }, [changedFiles]);

  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const toggleGroup = useCallback((id: string) => {
    setExpandedGroups((prev) => ({ ...prev, [id]: !(prev[id] ?? false) }));
  }, []);

  // Measure wall-clock work time for live runs (most accurate); fall back to
  // the turn's first→last message timestamps for transcripts loaded from state.
  const [measuredDurationMs, setMeasuredDurationMs] = useState<number | null>(null);
  const workStartRef = useRef<number | null>(null);
  const wasStreamingRef = useRef(false);
  useEffect(() => {
    if (isStreaming) {
      if (workStartRef.current === null) workStartRef.current = Date.now();
      wasStreamingRef.current = true;
      return;
    }
    if (wasStreamingRef.current && workStartRef.current !== null) {
      setMeasuredDurationMs(Date.now() - workStartRef.current);
      wasStreamingRef.current = false;
    }
  }, [isStreaming]);

  const workDurationMs = useMemo(() => {
    if (measuredDurationMs !== null) return measuredDurationMs;
    if (!message.startedAt || message.timestampIsFallback) return null;
    const start = Date.parse(message.startedAt);
    const end = Date.parse(message.timestamp);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
    const delta = end - start;
    return delta > 0 ? delta : null;
  }, [measuredDurationMs, message.startedAt, message.timestamp, message.timestampIsFallback]);

  const { workItems, replyItems } = useMemo(() => splitWorkAndReply(renderItems), [renderItems]);
  const replyText = useMemo(
    () =>
      replyItems
        .map((item) =>
          item.type === "text-chunk" && item.chunk.kind === "text" ? item.chunk.text : "",
        )
        .join("")
        .trim(),
    [replyItems],
  );
  const canFoldWork = !isStreaming && workItems.length > 0;
  const [workFoldExpanded, setWorkFoldExpanded] = useState(false);
  const toggleWorkFold = useCallback(() => setWorkFoldExpanded((value) => !value), []);

  const renderItem = (item: RenderItem, index: number, total: number): ReactNode => {
    switch (item.type) {
      case "reasoning-item": {
        const reasoningChunk = item.chunk.kind === "reasoning" ? item.chunk : null;
        return (
          <div key={item.key} className="min-w-0 flex-1">
            <ReasoningBlock
              text={reasoningChunk?.text ?? ""}
              isLive={!!isStreaming && index === total - 1}
            />
          </div>
        );
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
        );

      case "subagent-group":
        return <SubagentGroup key={item.key} chunks={item.chunks} />;

      case "edit-item": {
        const diff = latestDiff(item.chunk);
        return (
          <EditWorkEntry
            key={item.key}
            chunk={item.chunk}
            projectPath={projectPath}
            resolved={diff ? changedFilesByPath.get(diff.filePath) : undefined}
          />
        );
      }

      case "shell-item":
        return (
          <WorkEntryRow
            key={item.key}
            entry={describeWorkEntry(item.chunk, projectPath)}
            timestamp={item.chunk.timestamp}
            body={<ShellEntryBody chunk={item.chunk} />}
            defaultExpanded={item.chunk.status === "in_progress"}
          />
        );

      case "reply-item":
        return <ReplyCard key={item.key} chunk={item.chunk} />;

      case "tool-item":
        return (
          <WorkEntryRow
            key={item.key}
            entry={describeWorkEntry(item.chunk, projectPath)}
            timestamp={item.chunk.timestamp}
          />
        );

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
        );
    }
  };

  const foldLabel =
    workDurationMs && workDurationMs >= 1000
      ? `Worked for ${formatElapsed(workDurationMs)}`
      : "Worked";

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
              {workItems.map((item, index) => renderItem(item, index, workItems.length))}
            </div>
          )}
          {replyItems.map((item, index) =>
            renderItem(item, workItems.length + index, renderItems.length),
          )}
        </>
      ) : (
        renderItems.map((item, index) => renderItem(item, index, renderItems.length))
      )}

      {changedFiles.length > 0 && !isStreaming && (
        <TurnChangedFilesCard
          files={changedFiles}
          totals={changedFilesTotals}
          projectPath={projectPath}
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
          <MessageTimestamp timestamp={message.timestamp} startedAt={message.startedAt} />
        )}
      </div>
    </div>
  );
}
