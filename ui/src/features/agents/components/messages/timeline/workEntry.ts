import { formatJsonToolResult } from "./toolResultJson"
import type {
  AcpToolStatus,
  Chunk,
  ToolExecutionChunk,
} from "@/features/agents/lib/types"
import {
  formatPathDisplayParts,
  formatToolDisplayParts,
} from "@/features/agents/components/chat/toolExecutionDisplay"
import { countLineChanges } from "@/features/agents/utils/diffStats"

export type WorkEntryIconName =
  | "bot"
  | "check"
  | "circle-alert"
  | "eye"
  | "globe"
  | "hammer"
  | "message-circle"
  | "square-pen"
  | "terminal"
  | "wrench"
  | "zap"

export type WorkEntryTone = "tool" | "thinking" | "error" | "info"

export interface WorkEntryView {
  icon: WorkEntryIconName
  heading: string
  /** Dimmed argument shown after the heading; null when it would just repeat it. */
  preview: string | null
  previewTooltip?: string
  diffStats?: { additions: number; deletions: number }
  tone: WorkEntryTone
  status: AcpToolStatus
  /** Plain-text detail for rows that have no richer renderer of their own. */
  expandedText: string | null
}

function iconForChunk(chunk: ToolExecutionChunk): WorkEntryIconName {
  if (chunk.diffs?.length || chunk.diffData) return "square-pen"

  switch (chunk.toolKind) {
    case "execute":
      return "terminal"
    case "read":
      return "eye"
    case "search":
      return "eye"
    case "edit":
    case "delete":
    case "move":
      return "square-pen"
    case "fetch":
      return "globe"
    case "think":
      return "bot"
    case "slack":
    case "linear":
      return "message-circle"
    case "task":
      return "hammer"
    default:
      return "wrench"
  }
}

function toneForChunk(chunk: ToolExecutionChunk): WorkEntryTone {
  if (chunk.status === "error") return "error"
  if (chunk.toolKind === "think") return "thinking"
  return "tool"
}

function firstLocationPath(
  chunk: ToolExecutionChunk,
  projectPath?: string
): string | null {
  const locations = chunk.locations ?? []
  const first = locations[0]
  if (!first) return null
  const display = stripProjectPath(first.path, projectPath)
  return locations.length === 1
    ? display
    : `${display} +${locations.length - 1} more`
}

function stripProjectPath(path: string, projectPath?: string): string {
  if (!projectPath || !path.startsWith(projectPath)) return path
  return path.slice(projectPath.length).replace(/^\/+/, "") || "."
}

function normalizeForCompare(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase()
}

/**
 * Truncated so a runaway tool output can't blow up the row; the full text stays
 * reachable through the tool's own renderer where one exists.
 */
const MAX_EXPANDED_TEXT_LENGTH = 4000

function expandedTextForChunk(
  chunk: ToolExecutionChunk,
  projectPath?: string
): string | null {
  const blocks: Array<string> = []

  const command =
    typeof chunk.input?.command === "string" ? chunk.input.command.trim() : ""
  if (command) blocks.push(command)

  const rawOutput = chunk.output ?? ""
  const output = rawOutput.trim()
  const jsonOutput = formatJsonToolResult(rawOutput)
  if (output) blocks.push(jsonOutput ?? output)

  const locations = chunk.locations ?? []
  if (!output && locations.length > 0) {
    blocks.push(
      locations.map((loc) => stripProjectPath(loc.path, projectPath)).join("\n")
    )
  }

  if (blocks.length === 0) return null
  const joined = blocks.join("\n\n")
  if (jsonOutput !== null && !command) return joined
  return joined.length > MAX_EXPANDED_TEXT_LENGTH
    ? `${joined.slice(0, MAX_EXPANDED_TEXT_LENGTH)}\n…`
    : joined
}

/** The diff a tool call ultimately produced — the last one wins when a call touched a file repeatedly. */
export function latestDiff(chunk: ToolExecutionChunk) {
  return chunk.diffs?.length
    ? chunk.diffs[chunk.diffs.length - 1]
    : chunk.diffData
}

export function describeWorkEntry(
  chunk: ToolExecutionChunk,
  projectPath?: string
): WorkEntryView {
  const diff = latestDiff(chunk)
  if (diff) {
    const heading =
      chunk.status === "error"
        ? "Failed to edit"
        : chunk.status === "completed"
          ? diff.isNewFile
            ? "Created"
            : "Edited"
          : "Editing"
    const pathDisplay = formatPathDisplayParts(heading, diff.filePath)
    return {
      icon: "square-pen",
      ...pathDisplay,
      diffStats: countLineChanges(
        diff.originalContent,
        diff.newContent,
        diff.filePath
      ),
      tone: toneForChunk(chunk),
      status: chunk.status,
      // The diff itself is the body; a text dump alongside it would be noise.
      expandedText: null,
    }
  }

  const { heading, preview, previewTooltip } = formatToolDisplayParts(
    chunk.title,
    chunk.toolKind,
    chunk.input,
    projectPath
  )
  const resolvedPreview = preview ?? firstLocationPath(chunk, projectPath)

  return {
    icon: iconForChunk(chunk),
    heading,
    preview:
      resolvedPreview &&
      normalizeForCompare(resolvedPreview) !== normalizeForCompare(heading)
        ? resolvedPreview
        : null,
    previewTooltip,
    tone: toneForChunk(chunk),
    status: chunk.status,
    expandedText: expandedTextForChunk(chunk, projectPath),
  }
}

function toolActivityVerb(chunk: ToolExecutionChunk): string {
  const active = chunk.status === "in_progress" || chunk.status === "pending"

  switch (chunk.toolKind) {
    case "read":
    case "search":
      return active ? "Exploring" : "Explored"
    case "execute":
      return active ? "Running" : "Ran"
    case "edit":
    case "delete":
    case "move":
      return active ? "Editing" : "Updated"
    case "fetch":
      return active ? "Researching" : "Researched"
    case "task":
      return active ? "Delegating" : "Delegated"
    case "think":
      return "Thinking"
    case "slack":
    case "linear":
      return "Sending update"
    case "other":
      return describeWorkEntry(chunk).heading
  }
}

export function liveActivityLabel(
  chunks: Array<Chunk>,
  projectPath?: string
): string {
  for (let index = chunks.length - 1; index >= 0; index -= 1) {
    const chunk = chunks[index]
    if (!chunk) continue

    if (chunk.kind === "reasoning") return "Thinking…"
    if (chunk.kind === "text") {
      if (chunk.text.trim()) return "Writing response…"
      continue
    }
    if (chunk.kind === "error") return "Recovering from an error…"
    if (
      chunk.kind === "code" ||
      chunk.kind === "list" ||
      chunk.kind === "image" ||
      chunk.kind === "attachment"
    ) {
      return "Preparing response…"
    }
    if (chunk.kind === "todo") return "Planning next steps…"

    if (chunk.status === "pending") return "Waiting for approval…"
    if (chunk.status === "error") return "Recovering from an error…"
    if (chunk.display?.type === "output_iframe") return "Preparing preview…"

    const entry = describeWorkEntry(chunk, projectPath)
    const verb = toolActivityVerb(chunk)
    return entry.preview ? `${verb} · ${entry.preview}` : `${verb}…`
  }

  return "Working…"
}
