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
  | "brain"
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

export interface WorkEntryView {
  icon: WorkEntryIconName
  heading: string
  /** Dimmed argument shown after the heading; null when it would just repeat it. */
  preview: string | null
  previewTooltip?: string
  destinationHref?: string
  destinationLabel?: string
  diffStats?: { additions: number; deletions: number }
  status: AcpToolStatus | "unknown"
  /** The call's output as plain text, for rows that render nothing richer. */
  expandedText: string | null
  /**
   * Fetches the rest of the output when the row is expanded, for sources whose
   * transcript holds only a preview. Null when there is nothing more to get.
   */
  loadExpandedText: (() => Promise<string>) | null
}

function iconForChunk(chunk: ToolExecutionChunk): WorkEntryIconName {
  if (chunk.diffs?.length || chunk.diffData) return "square-pen"

  switch (chunk.toolKind) {
    case "execute":
    case "background":
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

function firstLocationPath(
  chunk: ToolExecutionChunk,
  repoPath?: string
): string | null {
  const locations = chunk.locations ?? []
  const first = locations[0]
  if (!first) return null
  const display = stripRepoPath(first.path, repoPath)
  return locations.length === 1
    ? display
    : `${display} +${locations.length - 1} more`
}

function stripRepoPath(path: string, repoPath?: string): string {
  if (!repoPath || !path.startsWith(repoPath)) return path
  return path.slice(repoPath.length).replace(/^\/+/, "") || "."
}

function normalizeForCompare(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase()
}

/**
 * Truncated so a runaway tool output can't blow up the row; the full text stays
 * reachable through the lazy load or the tool's own renderer.
 */
const MAX_EXPANDED_TEXT_LENGTH = 4000

function expandedTextForChunk(
  chunk: ToolExecutionChunk,
  repoPath?: string
): string | null {
  const output = (chunk.output ?? "").trim()
  if (!output) {
    const locations = chunk.locations ?? []
    return locations.length > 0
      ? locations.map((loc) => stripRepoPath(loc.path, repoPath)).join("\n")
      : null
  }
  const json = formatJsonToolResult(chunk.output ?? "")
  if (json !== null) return json
  return output.length > MAX_EXPANDED_TEXT_LENGTH
    ? `${output.slice(0, MAX_EXPANDED_TEXT_LENGTH)}\n…`
    : output
}

/** Whether a call's input/output body would show anything when expanded. */
export function hasToolCallDetail(
  chunk: ToolExecutionChunk,
  hasInput: boolean
): boolean {
  return (
    hasInput ||
    Boolean(chunk.output || chunk.loadOutput) ||
    chunk.status === "in_progress" ||
    chunk.status === "pending"
  )
}

/** The diff a tool call ultimately produced — the last one wins when a call touched a file repeatedly. */
export function latestDiff(chunk: ToolExecutionChunk) {
  return chunk.diffs?.length
    ? chunk.diffs[chunk.diffs.length - 1]
    : chunk.diffData
}

export function routedMessageHref(
  chunk: ToolExecutionChunk
): string | undefined {
  const name = (
    chunk.toolName ?? chunk.title.trim().split(/\s+/, 1)[0]
  )?.toLowerCase()
  if (
    chunk.status !== "completed" ||
    !(
      name === "message_task_thread" ||
      (name === "manage_thread" && chunk.input?.action === "send_message")
    )
  )
    return undefined
  try {
    const result: unknown = JSON.parse(chunk.output ?? "")
    if (
      typeof result === "object" &&
      result !== null &&
      "success" in result &&
      result.success === true &&
      "recipient_thread_id" in result &&
      typeof result.recipient_thread_id === "string" &&
      /^[0-9a-f-]{36}$/i.test(result.recipient_thread_id)
    )
      return `/agents/${result.recipient_thread_id}`
  } catch {
    return undefined
  }
  return undefined
}

export function describeWorkEntry(
  chunk: ToolExecutionChunk,
  repoPath?: string
): WorkEntryView {
  const diff = latestDiff(chunk)
  if (diff && chunk.status !== "error") {
    const heading =
      chunk.status === "completed"
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
      status: chunk.status,
      // The diff itself is the body; a text dump alongside it would be noise.
      expandedText: null,
      loadExpandedText: null,
    }
  }

  const { heading, preview, previewTooltip } = formatToolDisplayParts(
    chunk.title,
    chunk.toolKind,
    chunk.input,
    repoPath
  )
  const resolvedPreview = preview ?? firstLocationPath(chunk, repoPath)

  return {
    icon: iconForChunk(chunk),
    heading,
    preview:
      resolvedPreview &&
      normalizeForCompare(resolvedPreview) !== normalizeForCompare(heading)
        ? resolvedPreview
        : null,
    previewTooltip,
    destinationHref: routedMessageHref(chunk),
    status: chunk.status,
    expandedText: expandedTextForChunk(chunk, repoPath),
    loadExpandedText: chunk.loadOutput ?? null,
  }
}

function toolActivityVerb(chunk: ToolExecutionChunk): string {
  switch (chunk.toolKind) {
    case "read":
      return "Reading"
    case "search":
      return "Searching"
    case "sql":
      return "Querying"
    case "execute":
      return "Running"
    case "background":
      return "Starting in background"
    case "edit":
    case "delete":
    case "move":
      return "Editing"
    case "fetch":
      return "Fetching"
    case "task":
      return "Delegating"
    case "code":
      return "Running code"
    case "think":
      return "Thinking"
    case "slack":
    case "linear":
      return "Sending update"
    case "managed-tools":
      return "Offering connection"
    case "other":
      return describeWorkEntry(chunk).heading
  }
}

/** Live activity label and whether transition pacing should be bypassed. */
export class LiveActivity {
  readonly label: string
  readonly urgent: boolean

  constructor(label: string, urgent = false) {
    this.label = label
    this.urgent = urgent
  }

  static status(label: string, urgent = false): LiveActivity {
    return new LiveActivity(label, urgent)
  }

  private static resolveActiveTool(
    chunks: ReadonlyArray<Chunk>
  ): ToolExecutionChunk | undefined {
    let latestRunning: ToolExecutionChunk | undefined
    for (let index = chunks.length - 1; index >= 0; index -= 1) {
      const candidate = chunks[index]
      if (candidate?.kind !== "tool-execution") continue
      if (candidate.status === "pending") return candidate
      if (candidate.status === "in_progress") {
        if (candidate.toolKind === "code") {
          const pendingChild = candidate.calls?.findLast(
            (call) => call.status === "pending"
          )
          if (pendingChild) return pendingChild
        }
        latestRunning ??= candidate
      }
    }
    if (latestRunning?.toolKind === "code") {
      return (
        latestRunning.calls?.findLast(
          (call) => call.status === "in_progress"
        ) ?? latestRunning
      )
    }
    return latestRunning
  }

  static fromChunks(chunks: Array<Chunk>, repoPath?: string): LiveActivity {
    const activeTool = LiveActivity.resolveActiveTool(chunks)
    if (activeTool) {
      if (activeTool.status === "pending")
        return LiveActivity.status("Waiting for approval…", true)
      if (activeTool.display?.type === "output_iframe")
        return LiveActivity.status("Preparing preview…", true)

      const entry = describeWorkEntry(activeTool, repoPath)
      if (
        typeof activeTool.input?.display_description === "string" &&
        activeTool.input.display_description.trim() &&
        entry.preview
      ) {
        return LiveActivity.status(entry.preview)
      }
      if (activeTool.toolKind === "other") {
        return LiveActivity.status(`Using ${entry.heading}…`)
      }
      const verb = toolActivityVerb(activeTool)
      return LiveActivity.status(
        entry.preview ? `${verb} · ${entry.preview}` : `${verb}…`
      )
    }

    for (let index = chunks.length - 1; index >= 0; index -= 1) {
      const chunk = chunks[index]
      if (!chunk) continue
      if (chunk.kind === "text") {
        if (chunk.text.trim())
          return LiveActivity.status("Writing response…", true)
        continue
      }
      if (
        chunk.kind === "code" ||
        chunk.kind === "list" ||
        chunk.kind === "image"
      ) {
        return LiveActivity.status("Preparing response…", true)
      }
      return LiveActivity.status("Thinking…")
    }

    return LiveActivity.status("Thinking…")
  }
}

export function liveActivityLabel(
  chunks: Array<Chunk>,
  repoPath?: string
): string {
  return LiveActivity.fromChunks(chunks, repoPath).label
}
