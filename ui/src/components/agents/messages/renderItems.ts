import type { Chunk, ToolExecutionChunk } from "@/lib/agents/types";

export type RenderItem =
  | { type: "text-chunk"; key: string; chunk: Chunk }
  | { type: "reasoning-item"; key: string; chunk: Chunk }
  | { type: "explored-group"; key: string; id: string; chunks: ToolExecutionChunk[] }
  /**
   * One or more `task` (subagent) tool calls collapsed into a single group so
   * they can be rendered side by side as a card grid (see subagents/SubagentGroup).
   */
  | { type: "subagent-group"; key: string; id: string; chunks: ToolExecutionChunk[] }
  | { type: "edit-item"; key: string; chunk: ToolExecutionChunk }
  | { type: "shell-item"; key: string; chunk: ToolExecutionChunk }
  | { type: "reply-item"; key: string; chunk: ToolExecutionChunk }
  | { type: "tool-item"; key: string; chunk: ToolExecutionChunk };

function getChunkRenderKey(chunk: Chunk, sourceIndex: number): string {
  switch (chunk.kind) {
    case "tool-execution":
      return `tool-${chunk.toolCallId}`;
    case "text":
      return `text-${sourceIndex}`;
    case "reasoning":
      return `reasoning-${sourceIndex}`;
    case "code":
      return `code-${sourceIndex}`;
    case "error":
      return `error-${sourceIndex}`;
    case "list":
      return `list-${sourceIndex}`;
    case "image":
      return `image-${sourceIndex}`;
    default:
      return `chunk-${sourceIndex}`;
  }
}

function isEditTool(chunk: ToolExecutionChunk): boolean {
  const kind = chunk.toolKind;
  if (kind === "edit" || kind === "delete" || kind === "move") return true;
  if (chunk.diffs?.length) return true;
  if (chunk.diffData) return true;
  return false;
}

function isExplorationTool(chunk: ToolExecutionChunk): boolean {
  if (chunk.diffs?.length) return false;
  if (chunk.diffData) return false;
  const kind = chunk.toolKind;
  return kind === "read" || kind === "search";
}

function isShellTool(chunk: ToolExecutionChunk): boolean {
  return chunk.toolKind === "execute";
}

function isReplyTool(chunk: ToolExecutionChunk): boolean {
  return chunk.toolKind === "slack" || chunk.toolKind === "linear";
}

/**
 * Whether a tool chunk represents a spawned subagent. Subagents are launched
 * via deepagents' `task` tool, which the transcript builder
 * (`streamMessagesToUi.ts`) tags as `toolKind: "task"`.
 * These are grouped and rendered as cards instead of a plain tool line.
 */
function isSubagentTool(chunk: ToolExecutionChunk): boolean {
  return chunk.toolKind === "task";
}

export function buildRenderItems(chunks: Chunk[], messageId?: string): RenderItem[] {
  const items: RenderItem[] = [];
  let exploredBuffer: ToolExecutionChunk[] = [];
  let exploredStartIndex = -1;
  let subagentBuffer: ToolExecutionChunk[] = [];
  let subagentStartIndex = -1;

  const flushExplored = () => {
    if (exploredBuffer.length === 0) return;
    const firstId = exploredBuffer[0]?.toolCallId;
    const id = `explored-${firstId || exploredStartIndex}`;
    items.push({
      type: "explored-group",
      key: id,
      id,
      chunks: [...exploredBuffer],
    });
    exploredBuffer = [];
    exploredStartIndex = -1;
  };

  const flushSubagents = () => {
    if (subagentBuffer.length === 0) return;
    const firstId = subagentBuffer[0]?.toolCallId;
    const id = `subagents-${firstId || subagentStartIndex}`;
    items.push({
      type: "subagent-group",
      key: id,
      id,
      chunks: [...subagentBuffer],
    });
    subagentBuffer = [];
    subagentStartIndex = -1;
  };

  const flushGroups = () => {
    flushExplored();
    flushSubagents();
  };

  for (let i = 0; i < chunks.length; i += 1) {
    const chunk = chunks[i];
    if (!chunk) continue;

    if (chunk.kind === "tool-execution") {
      if (isSubagentTool(chunk)) {
        flushExplored();
        if (subagentBuffer.length === 0) subagentStartIndex = i;
        subagentBuffer.push(chunk);
        continue;
      }

      if (isExplorationTool(chunk)) {
        flushSubagents();
        if (exploredBuffer.length === 0) exploredStartIndex = i;
        exploredBuffer.push(chunk);
        continue;
      }

      flushGroups();

      if (isEditTool(chunk)) {
        items.push({ type: "edit-item", key: `tool-${chunk.toolCallId}`, chunk });
      } else if (isShellTool(chunk)) {
        items.push({ type: "shell-item", key: `tool-${chunk.toolCallId}`, chunk });
      } else if (isReplyTool(chunk)) {
        items.push({ type: "reply-item", key: `tool-${chunk.toolCallId}`, chunk });
      } else {
        items.push({ type: "tool-item", key: `tool-${chunk.toolCallId}`, chunk });
      }
      continue;
    }

    if (chunk.kind === "text" && !chunk.text.trim()) continue;

    if (chunk.kind === "reasoning") {
      flushGroups();
      items.push({
        type: "reasoning-item",
        key: messageId ? `${messageId}-${getChunkRenderKey(chunk, i)}` : getChunkRenderKey(chunk, i),
        chunk,
      });
      continue;
    }

    flushGroups();
    items.push({
      type: "text-chunk",
      key: messageId ? `${messageId}-${getChunkRenderKey(chunk, i)}` : getChunkRenderKey(chunk, i),
      chunk,
    });
  }

  flushGroups();
  return items;
}

export function summarizeExploration(chunks: ToolExecutionChunk[]): string {
  const count = chunks.length;
  return `Explored ${count} file${count === 1 ? "" : "s"}`;
}
