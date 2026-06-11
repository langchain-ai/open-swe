import { useEffect, useMemo, useRef, useState } from "react";

import { ReplyCard } from "@/components/agents/ported/ReplyCard";
import { ShellCommand } from "@/components/agents/ported/ShellCommand";
import { ToolExecution } from "@/components/agents/ported/ToolExecution";
import type { Message } from "@/lib/agents/types";

import { ChunkRenderer } from "./ChunkRenderer";
import { ReasoningBlock } from "./ReasoningBlock";
import { buildRenderItems, summarizeExploration, type RenderItem } from "./renderItems";
import { SubagentGroup } from "@/components/agents/subagents";
import { summarizeChangedFiles } from "./summarizeChangedFiles";
import { TurnChangedFilesCard } from "./TurnChangedFilesCard";
import type { ApprovalCallbacks, ChangedFileSummaryItem } from "./types";

export function AgentMessage({
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
    for (const file of changedFiles) {
      byPath.set(file.filePath, file);
    }
    return byPath;
  }, [changedFiles]);

  const exploredGroupIds = useMemo(
    () =>
      renderItems
        .filter((item): item is Extract<RenderItem, { type: "explored-group" }> => item.type === "explored-group")
        .map((item) => item.id),
    [renderItems],
  );
  const hasExploredGroups = exploredGroupIds.length > 0;
  const [expandedExploredGroups, setExpandedExploredGroups] = useState<Record<string, boolean>>({});
  const wasExplorationLiveRef = useRef(false);

  useEffect(() => {
    const isLive = !!isStreaming;

    if (!hasExploredGroups) {
      setExpandedExploredGroups({});
      wasExplorationLiveRef.current = isLive;
      return;
    }

    if (isLive) {
      const next: Record<string, boolean> = {};
      for (const id of exploredGroupIds) {
        next[id] = true;
      }
      setExpandedExploredGroups(next);
      wasExplorationLiveRef.current = true;
      return;
    }

    const shouldAutoCollapse = wasExplorationLiveRef.current;
    setExpandedExploredGroups((prev) => {
      const next: Record<string, boolean> = {};
      for (const id of exploredGroupIds) {
        next[id] = shouldAutoCollapse ? false : (prev[id] ?? false);
      }

      const prevKeys = Object.keys(prev);
      const nextKeys = Object.keys(next);
      if (prevKeys.length !== nextKeys.length) return next;
      for (const key of nextKeys) {
        if (prev[key] !== next[key]) return next;
      }
      return prev;
    });
    wasExplorationLiveRef.current = false;
  }, [hasExploredGroups, isStreaming, exploredGroupIds, message.id]);

  return (
    <div className="my-2 min-w-0 space-y-2">
      {renderItems.map((item, index) => {
        switch (item.type) {
          case "reasoning-item": {
            const reasoningChunk = item.chunk.kind === "reasoning" ? item.chunk : null;
            const isLastItem = index === renderItems.length - 1;
            return (
              <div key={item.key} className="flex-1 min-w-0">
                <ReasoningBlock
                  text={reasoningChunk?.text ?? ""}
                  isLive={!!isStreaming && isLastItem}
                />
              </div>
            );
          }

          case "explored-group": {
            const summary = summarizeExploration(item.chunks);
            const isExpanded = expandedExploredGroups[item.id] ?? false;
            return (
              <div key={item.key}>
                <button
                  type="button"
                  onClick={() =>
                    setExpandedExploredGroups((prev) => ({
                      ...prev,
                      [item.id]: !(prev[item.id] ?? false),
                    }))
                  }
                  className="w-full flex items-center justify-between py-1 text-left hover:opacity-90 transition-opacity"
                >
                  <span className="text-[color:var(--ui-text-muted)] text-[12px]">{summary}</span>
                  <span className="text-[color:var(--ui-text-dim)] text-xs">{isExpanded ? "Hide" : "Show"}</span>
                </button>
                {isExpanded && (
                  <div className="pt-1 pb-1 space-y-0.5">
                    {item.chunks.map((chunk, chunkIndex) => (
                      <div key={chunk.toolCallId || `explored-chunk-${item.id}-${chunkIndex}`} className="flex-1 min-w-0 text-gray-500">
                        <ToolExecution
                          chunk={chunk}
                          projectPath={projectPath}
                          onOpenDiff={callbacks.onOpenDiff}
                        />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          }

          case "subagent-group":
            return <SubagentGroup key={item.key} chunks={item.chunks} />;

          case "edit-item": {
            const fullFileDiff = item.chunk.diffData
              ? changedFilesByPath.get(item.chunk.diffData.filePath)
              : undefined;

            return (
              <div key={item.key}>
                <ToolExecution
                  chunk={item.chunk}
                  projectPath={projectPath}
                  onApprove={callbacks.onApprove}
                  onReject={callbacks.onReject}
                  onAutoApprove={callbacks.onAutoApprove}
                  onOpenDiff={callbacks.onOpenDiff}
                  resolvedDiffData={fullFileDiff ? {
                    originalContent: fullFileDiff.originalContent,
                    modifiedContent: fullFileDiff.modifiedContent,
                  } : undefined}
                />
              </div>
            );
          }

          case "shell-item":
            return (
              <div key={item.key}>
                <ShellCommand
                  chunk={item.chunk}
                  projectPath={projectPath}
                />
              </div>
            );

          case "reply-item":
            return (
              <div key={item.key}>
                <ReplyCard chunk={item.chunk} />
              </div>
            );

          case "tool-item":
            return (
              <div key={item.key}>
                <ToolExecution
                  chunk={item.chunk}
                  projectPath={projectPath}
                  onApprove={callbacks.onApprove}
                  onReject={callbacks.onReject}
                  onAutoApprove={callbacks.onAutoApprove}
                  onOpenDiff={callbacks.onOpenDiff}
                />
              </div>
            );

          case "text-chunk":
            return (
              <div key={item.key} className="flex-1 min-w-0">
                <ChunkRenderer
                  chunk={item.chunk}
                  projectPath={projectPath}
                  isMarkdownLive={isMarkdownLive}
                  {...callbacks}
                />
              </div>
            );
        }
      })}

      {changedFiles.length > 0 && !isStreaming && (
        <TurnChangedFilesCard
          files={changedFiles}
          totals={changedFilesTotals}
          projectPath={projectPath}
        />
      )}
    </div>
  );
}
