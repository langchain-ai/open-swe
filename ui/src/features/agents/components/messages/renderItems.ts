import type { Chunk, ToolExecutionChunk } from "@/features/agents/lib/types"

export type RenderItem =
  | { type: "text-chunk"; key: string; chunk: Chunk }
  | { type: "reasoning-item"; key: string; chunk: Chunk }
  | {
      type: "explored-group"
      key: string
      id: string
      chunks: Array<ToolExecutionChunk>
    }
  /**
   * One or more `task` (subagent) tool calls collapsed into a single group so
   * they can be rendered side by side as a card grid (see subagents/SubagentGroup).
   */
  | {
      type: "subagent-group"
      key: string
      id: string
      chunks: Array<ToolExecutionChunk>
    }
  | { type: "edit-item"; key: string; chunk: ToolExecutionChunk }
  | {
      type: "edit-group"
      key: string
      chunks: Array<ToolExecutionChunk>
    }
  | {
      type: "read-group"
      key: string
      chunks: Array<ToolExecutionChunk>
    }
  | { type: "shell-item"; key: string; chunk: ToolExecutionChunk }
  | { type: "reply-item"; key: string; chunk: ToolExecutionChunk }
  | { type: "iframe-item"; key: string; chunk: ToolExecutionChunk }
  | { type: "tool-item"; key: string; chunk: ToolExecutionChunk }

const REPLY_ITEM_TYPES = new Set<RenderItem["type"]>([
  "text-chunk",
  "reply-item",
  "iframe-item",
])

export function splitWorkAndReply(items: Array<RenderItem>): {
  workItems: Array<RenderItem>
  replyItems: Array<RenderItem>
} {
  let trailingReplyIndex = items.length
  while (trailingReplyIndex > 0) {
    const prev = items[trailingReplyIndex - 1]
    if (!prev || !REPLY_ITEM_TYPES.has(prev.type)) break
    trailingReplyIndex -= 1
  }

  const workItems: Array<RenderItem> = []
  const replyItems: Array<RenderItem> = []
  items.forEach((item, index) => {
    if (
      item.type === "reply-item" ||
      item.type === "iframe-item" ||
      index >= trailingReplyIndex
    ) {
      replyItems.push(item)
    } else {
      workItems.push(item)
    }
  })
  return { workItems, replyItems }
}

function toolNeedsAttention(
  chunk: ToolExecutionChunk,
  includeUnfinished: boolean
): boolean {
  return (
    chunk.status === "pending" ||
    (includeUnfinished && chunk.status === "in_progress")
  )
}

function attentionItems(
  item: RenderItem,
  includeUnfinished: boolean
): Array<RenderItem> {
  if (item.type === "edit-group" || item.type === "read-group") {
    return item.chunks.some((chunk) =>
      toolNeedsAttention(chunk, includeUnfinished)
    )
      ? [item]
      : []
  }

  if (item.type === "explored-group" || item.type === "subagent-group") {
    return item.chunks
      .filter((chunk) => toolNeedsAttention(chunk, includeUnfinished))
      .map((chunk) => ({
        type: "tool-item" as const,
        key: `attention-${chunk.toolCallId}`,
        chunk,
      }))
  }

  if (
    item.type === "edit-item" ||
    item.type === "shell-item" ||
    item.type === "tool-item"
  ) {
    return toolNeedsAttention(item.chunk, includeUnfinished) ? [item] : []
  }

  if (item.type === "text-chunk" && item.chunk.kind === "error") {
    return [item]
  }

  return []
}

export function selectCollapsedTurnItems(
  items: Array<RenderItem>,
  includeUnfinished = false
): Array<RenderItem> {
  const { replyItems } = splitWorkAndReply(items)
  const replyKeys = new Set(replyItems.map((item) => item.key))

  return items.flatMap((item) =>
    replyKeys.has(item.key) ? [item] : attentionItems(item, includeUnfinished)
  )
}

export function countWorkActions(items: Array<RenderItem>): number {
  return items.reduce((count, item) => {
    if (item.type === "explored-group" || item.type === "subagent-group") {
      return count + item.chunks.length
    }
    if (item.type === "edit-group" || item.type === "read-group") {
      return count + item.chunks.length
    }
    if (
      item.type === "edit-item" ||
      item.type === "shell-item" ||
      item.type === "tool-item"
    ) {
      return count + 1
    }
    return count
  }, 0)
}

function getChunkRenderKey(chunk: Chunk, sourceIndex: number): string {
  switch (chunk.kind) {
    case "tool-execution":
      return `tool-${chunk.toolCallId}`
    case "text":
      return `text-${sourceIndex}`
    case "reasoning":
      return `reasoning-${sourceIndex}`
    case "code":
      return `code-${sourceIndex}`
    case "error":
      return `error-${sourceIndex}`
    case "list":
      return `list-${sourceIndex}`
    case "image":
      return `image-${sourceIndex}`
    default:
      return `chunk-${sourceIndex}`
  }
}

function isEditTool(chunk: ToolExecutionChunk): boolean {
  const kind = chunk.toolKind
  if (kind === "edit" || kind === "delete" || kind === "move") return true
  if (chunk.diffs?.length) return true
  if (chunk.diffData) return true
  return false
}

function isExplorationTool(chunk: ToolExecutionChunk): boolean {
  if (chunk.diffs?.length) return false
  if (chunk.diffData) return false
  const kind = chunk.toolKind
  return kind === "read" || kind === "search"
}

function isShellTool(chunk: ToolExecutionChunk): boolean {
  return chunk.toolKind === "execute"
}

function isReplyTool(chunk: ToolExecutionChunk): boolean {
  return chunk.toolKind === "slack" || chunk.toolKind === "linear"
}

/**
 * Whether a tool chunk represents a spawned subagent. Subagents are launched
 * via deepagents' `task` tool, which the transcript builder
 * (`streamMessagesToUi.ts`) tags as `toolKind: "task"`.
 * These are grouped and rendered as cards instead of a plain tool line.
 */
function isSubagentTool(chunk: ToolExecutionChunk): boolean {
  return chunk.toolKind === "task"
}

function toolFilePath(chunk: ToolExecutionChunk): string | null {
  const diff = chunk.diffs?.length
    ? chunk.diffs[chunk.diffs.length - 1]
    : chunk.diffData
  if (diff?.filePath) return diff.filePath
  const path =
    chunk.input?.file_path ?? chunk.input?.path ?? chunk.input?.target_file
  return typeof path === "string" && path.trim() ? path.trim() : null
}

function collapseRepeatedEdits(items: Array<RenderItem>): Array<RenderItem> {
  const groups = new Map<string, Array<ToolExecutionChunk>>()
  for (const item of items) {
    if (item.type !== "edit-item") continue
    const path = toolFilePath(item.chunk)
    if (!path) continue
    const group = groups.get(path) ?? []
    group.push(item.chunk)
    groups.set(path, group)
  }

  const renderedGroups = new Set<string>()
  return items.flatMap<RenderItem>((item) => {
    if (item.type !== "edit-item") return [item]
    const path = toolFilePath(item.chunk)
    const chunks = path ? groups.get(path) : undefined
    if (!path || !chunks || chunks.length === 1) return [item]
    if (renderedGroups.has(path)) return []
    renderedGroups.add(path)
    return [
      {
        type: "edit-group" as const,
        key: `edit-group-${item.chunk.toolCallId}`,
        chunks,
      },
    ]
  })
}

function collapseRepeatedReads(items: Array<RenderItem>): Array<RenderItem> {
  const groups = new Map<string, Array<ToolExecutionChunk>>()
  for (const item of items) {
    if (item.type !== "explored-group") continue
    for (const chunk of item.chunks) {
      if (chunk.toolKind !== "read") continue
      const path = toolFilePath(chunk)
      if (!path) continue
      const group = groups.get(path) ?? []
      group.push(chunk)
      groups.set(path, group)
    }
  }

  const renderedGroups = new Set<string>()
  return items.flatMap<RenderItem>((item) => {
    if (item.type !== "explored-group") return [item]
    const replacement: Array<RenderItem> = []
    let remaining: Array<ToolExecutionChunk> = []
    const flushRemaining = () => {
      if (remaining.length === 0) return
      const first = remaining[0]
      replacement.push({
        type: "explored-group",
        key: `${item.key}-${first?.toolCallId ?? replacement.length}`,
        id: `${item.id}-${first?.toolCallId ?? replacement.length}`,
        chunks: remaining,
      })
      remaining = []
    }

    for (const chunk of item.chunks) {
      const path = chunk.toolKind === "read" ? toolFilePath(chunk) : null
      const chunks = path ? groups.get(path) : undefined
      if (!path || !chunks || chunks.length === 1) {
        remaining.push(chunk)
        continue
      }
      if (renderedGroups.has(path)) continue
      flushRemaining()
      renderedGroups.add(path)
      replacement.push({
        type: "read-group",
        key: `read-group-${chunk.toolCallId}`,
        chunks,
      })
    }
    flushRemaining()
    return replacement
  })
}

export function buildRenderItems(
  chunks: Array<Chunk>,
  messageId?: string
): Array<RenderItem> {
  const items: Array<RenderItem> = []
  let exploredBuffer: Array<ToolExecutionChunk> = []
  let exploredStartIndex = -1
  let subagentBuffer: Array<ToolExecutionChunk> = []
  let subagentStartIndex = -1

  const flushExplored = () => {
    if (exploredBuffer.length === 0) return
    const firstId = exploredBuffer[0]?.toolCallId
    const id = `explored-${firstId || exploredStartIndex}`
    items.push({
      type: "explored-group",
      key: id,
      id,
      chunks: [...exploredBuffer],
    })
    exploredBuffer = []
    exploredStartIndex = -1
  }

  const flushSubagents = () => {
    if (subagentBuffer.length === 0) return
    const firstId = subagentBuffer[0]?.toolCallId
    const id = `subagents-${firstId || subagentStartIndex}`
    items.push({
      type: "subagent-group",
      key: id,
      id,
      chunks: [...subagentBuffer],
    })
    subagentBuffer = []
    subagentStartIndex = -1
  }

  const flushGroups = () => {
    flushExplored()
    flushSubagents()
  }

  for (let i = 0; i < chunks.length; i += 1) {
    const chunk = chunks[i]
    if (!chunk) continue

    if (chunk.kind === "tool-execution") {
      if (chunk.display?.type === "output_iframe") {
        flushGroups()
        items.push({
          type: "iframe-item",
          key: `tool-${chunk.toolCallId}`,
          chunk,
        })
        continue
      }

      if (isSubagentTool(chunk)) {
        flushExplored()
        if (subagentBuffer.length === 0) subagentStartIndex = i
        subagentBuffer.push(chunk)
        continue
      }

      if (isExplorationTool(chunk)) {
        flushSubagents()
        if (exploredBuffer.length === 0) exploredStartIndex = i
        exploredBuffer.push(chunk)
        continue
      }

      flushGroups()

      if (isEditTool(chunk)) {
        items.push({
          type: "edit-item",
          key: `tool-${chunk.toolCallId}`,
          chunk,
        })
      } else if (isShellTool(chunk)) {
        items.push({
          type: "shell-item",
          key: `tool-${chunk.toolCallId}`,
          chunk,
        })
      } else if (isReplyTool(chunk)) {
        items.push({
          type: "reply-item",
          key: `tool-${chunk.toolCallId}`,
          chunk,
        })
      } else {
        items.push({
          type: "tool-item",
          key: `tool-${chunk.toolCallId}`,
          chunk,
        })
      }
      continue
    }

    if (chunk.kind === "text" && !chunk.text.trim()) continue

    if (chunk.kind === "reasoning") {
      flushGroups()
      items.push({
        type: "reasoning-item",
        key: messageId
          ? `${messageId}-${getChunkRenderKey(chunk, i)}`
          : getChunkRenderKey(chunk, i),
        chunk,
      })
      continue
    }

    flushGroups()
    items.push({
      type: "text-chunk",
      key: messageId
        ? `${messageId}-${getChunkRenderKey(chunk, i)}`
        : getChunkRenderKey(chunk, i),
      chunk,
    })
  }

  flushGroups()
  return collapseRepeatedReads(collapseRepeatedEdits(items))
}

export function summarizeExploration(
  chunks: Array<ToolExecutionChunk>
): string {
  const count = chunks.length
  return `Explored ${count} file${count === 1 ? "" : "s"}`
}
