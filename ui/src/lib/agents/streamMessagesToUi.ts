import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import type { BaseMessage, ContentBlock } from "@langchain/core/messages";
import type { AssembledToolCall, SubagentDiscoverySnapshot } from "@langchain/react";

import type { Chunk, DiffData, Message, ToolExecutionChunk } from "./types";

const READ_TOOLS = new Set(["read_file", "read", "glob", "grep"]);
const EDIT_TOOLS = new Set(["write_file", "edit_file", "str_replace", "write", "edit", "patch"]);
const EXECUTE_TOOLS = new Set(["execute", "bash", "shell", "run_terminal_cmd"]);
const SEARCH_TOOLS = new Set(["glob", "grep", "web_search", "fetch_url", "search"]);
const INTERNAL_TOOLS = new Set(["confirming_completion", "no_op"]);

type ToolKind = ToolExecutionChunk["toolKind"];

function toolKind(name: string): ToolKind {
  const lowered = name.toLowerCase();
  // deepagents' subagent spawner — surfaced as a subagent card in Messages.
  if (lowered === "task") return "task";
  if (lowered === "slack_thread_reply") return "slack";
  if (lowered === "linear_comment") return "linear";
  if (EDIT_TOOLS.has(lowered) || ["edit", "write", "replace"].some((t) => lowered.includes(t))) {
    return "edit";
  }
  if (EXECUTE_TOOLS.has(lowered)) return "execute";
  if (SEARCH_TOOLS.has(lowered)) return "search";
  if (READ_TOOLS.has(lowered) || lowered.includes("read")) return "read";
  if (lowered === "think") return "think";
  if (["fetch", "fetch_url", "http_request"].includes(lowered)) return "fetch";
  return "other";
}

function toolTitle(name: string, args: Record<string, unknown>): string {
  const path = args.path ?? args.file_path ?? args.target_file;
  if (typeof path === "string" && path.trim()) return `${name} ${path.trim()}`;
  const command = args.command;
  if (typeof command === "string" && command.trim()) {
    return command.trim().split("\n")[0]?.slice(0, 120) ?? "";
  }
  return name.replace(/_/g, " ").trim() || "Tool";
}

function parseToolArgs(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : { raw };
    } catch {
      return { raw };
    }
  }
  return {};
}

function maybeDiffFromArgs(args: Record<string, unknown>): DiffData | null {
  const path = args.path ?? args.file_path ?? args.target_file;
  if (typeof path !== "string" || !path.trim()) return null;
  const oldContent = args.old_string ?? args.original_content;
  const newContent = args.new_string ?? args.content ?? args.new_content;
  if (typeof newContent !== "string") return null;
  const original = typeof oldContent === "string" ? oldContent : null;
  return {
    originalContent: original,
    newContent,
    filePath: path.trim(),
    isNewFile: original === null,
    isBinary: false,
    isTruncated: false,
    totalLines: Math.max(newContent.split("\n").length, 1),
  };
}

function mergeTextChunks(chunks: Array<Chunk>): Array<Chunk> {
  const textIndices = chunks.flatMap((c, i) => (c.kind === "text" ? [i] : []));
  if (textIndices.length <= 1) return chunks;
  const lastText = textIndices[textIndices.length - 1];
  return chunks.filter((c, i) => c.kind !== "text" || i === lastText);
}

type AgentTurn = { id: string; author: Message["author"]; timestamp: string; chunks: Array<Chunk> };

function messageTimestamp(raw: BaseMessage): string {
  const msg = raw as unknown as Record<string, unknown>;
  const createdAt = msg.created_at;
  if (typeof createdAt === "string" && createdAt) return createdAt;
  const responseMetadata = msg.response_metadata;
  if (responseMetadata && typeof responseMetadata === "object") {
    const metadataCreatedAt = (responseMetadata as Record<string, unknown>).created_at;
    if (typeof metadataCreatedAt === "string" && metadataCreatedAt) return metadataCreatedAt;
  }
  return new Date().toISOString();
}

/**
 * Pull reasoning ("thinking") text out of a message's standard content blocks.
 * `@langchain/core` v1 normalizes provider-specific reasoning (Anthropic
 * `thinking`, OpenAI reasoning, …) into `{ type: "reasoning", reasoning }`
 * blocks via the `contentBlocks` getter, so we don't have to parse each
 * provider's raw shape ourselves.
 */
function reasoningText(raw: BaseMessage): string {
  let blocks: Array<ContentBlock.Standard>;
  try {
    blocks = raw.contentBlocks;
  } catch {
    return "";
  }
  let text = "";
  for (const block of blocks) {
    if (block.type !== "reasoning") continue;
    // Reasoning blocks can arrive without a summary (e.g. OpenAI reasoning
    // models emit `{ type: "reasoning", extras: { content: [] } }` with no
    // `reasoning` field) — skip those so we don't render a "Thought" block
    // whose body is the literal string "undefined".
    const reasoning: unknown = block.reasoning;
    if (typeof reasoning === "string") text += reasoning;
  }
  return text.trim();
}

function imageChunks(content: unknown): Array<Chunk> {
  if (!Array.isArray(content)) return [];

  const chunks: Array<Chunk> = [];
  for (const item of content) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const block = item as Record<string, unknown>;
    const type = block.type;
    let base64: string | undefined;
    let mimeType: string | undefined;

    if (type === "image") {
      const data = block.data ?? block.base64;
      const mime = block.mime_type ?? block.mimeType;
      if (typeof data === "string" && typeof mime === "string") {
        base64 = data;
        mimeType = mime;
      }
    } else if (type === "image_url") {
      const imageUrl = block.image_url;
      const url =
        imageUrl && typeof imageUrl === "object"
          ? (imageUrl as Record<string, unknown>).url
          : undefined;
      if (typeof url === "string") {
        const match = /^data:(image\/[^;]+);base64,(.+)$/s.exec(url);
        if (match) {
          mimeType = match[1];
          base64 = match[2];
        }
      }
    }

    if (base64 && mimeType) {
      const fileName = block.fileName ?? block.file_name;
      chunks.push({
        kind: "image",
        base64,
        mimeType,
        ...(typeof fileName === "string" && fileName ? { fileName } : {}),
      });
    }
  }
  return chunks;
}

/**
 * Map the SDK's assembled tool-call lifecycle status onto the UI status.
 * `stream.toolCalls` exposes a fully-assembled, reactive view of each call
 * ({@link AssembledToolCall}) so we no longer hand-match AI `tool_calls` to
 * their `ToolMessage` results to derive status/output.
 */
function toolStatus(
  assembled: AssembledToolCall | undefined,
  toolMessage: ToolMessage | undefined,
): ToolExecutionChunk["status"] {
  if (assembled) {
    if (assembled.status === "finished") return "completed";
    if (assembled.status === "error") return "error";
    return "in_progress";
  }
  if (toolMessage) return toolMessage.status === "error" ? "error" : "completed";
  return "in_progress";
}

/** Map a {@link SubagentDiscoverySnapshot}'s lifecycle to the UI tool status. */
function subagentStatus(
  snapshot: SubagentDiscoverySnapshot,
): ToolExecutionChunk["status"] {
  if (snapshot.status === "complete") return "completed";
  if (snapshot.status === "error") return "error";
  return "in_progress";
}

function toolOutputText(
  assembled: AssembledToolCall | undefined,
  toolMessage: ToolMessage | undefined,
): string | undefined {
  const value = assembled?.output;
  if (value != null) {
    if (typeof value === "string") return value.trim() || undefined;
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  const text = toolMessage?.text.trim();
  return text || undefined;
}

/**
 * Read a server-computed diff off a tool result's persisted `artifact`.
 *
 * `ToolMessage.artifact` survives the checkpoint + `/state` hydration (it is
 * a standard serialized field), so `ToolArtifactMiddleware`
 * (`agent/middleware/tool_artifact.py`) attaches a real, sandbox-computed
 * {@link DiffData} here and the client renders it both live and on reload —
 * without re-deriving it from tool args. Falls back to the args heuristic
 * ({@link maybeDiffFromArgs}) when no artifact is present.
 */
function diffFromArtifact(artifact: unknown): DiffData | null {
  if (!artifact || typeof artifact !== "object") return null;
  const record = artifact as Record<string, unknown>;
  const candidate: unknown = record.diff ?? record.diffData ?? artifact;
  if (candidate === null || typeof candidate !== "object") return null;
  const diff = candidate as Record<string, unknown>;
  const { filePath, newContent } = diff;
  if (typeof filePath !== "string" || typeof newContent !== "string") return null;
  // The server may send a minimal `{ filePath, originalContent, newContent }`,
  // so fill the remaining presentation fields with sensible defaults.
  const originalContent = typeof diff.originalContent === "string" ? diff.originalContent : null;
  return {
    originalContent,
    newContent,
    filePath: filePath.trim(),
    isNewFile: typeof diff.isNewFile === "boolean" ? diff.isNewFile : originalContent === null,
    isBinary: typeof diff.isBinary === "boolean" ? diff.isBinary : false,
    isTruncated: typeof diff.isTruncated === "boolean" ? diff.isTruncated : false,
    totalLines:
      typeof diff.totalLines === "number"
        ? diff.totalLines
        : Math.max(newContent.split("\n").length, 1),
  };
}

/**
 * Convert the SDK's live projections into the dashboard chunk model so the
 * transcript streams (and hydrates) directly from the SDK instead of a
 * hand-rolled, server-mirrored adapter.
 *
 * - `messages` ({@link BaseMessage}[]) drives ordering, text, and reasoning.
 * - `toolCalls` ({@link AssembledToolCall}[], i.e. `stream.toolCalls`) drives
 *   each tool call's status and output — no `pendingTools` bookkeeping.
 * - `toolKind` / `title` stay a pure mapping of name+args (known at call time,
 *   already persisted) so the in-progress card renders instantly.
 * - `diffData` prefers the persisted `ToolMessage.artifact`, falling back to
 *   the args heuristic.
 * - `subagents` ({@link SubagentDiscoverySnapshot}[], i.e. `stream.subagents`)
 *   authoritatively identifies `task` calls that spawned a subagent (matched by
 *   `snapshot.id === toolCallId`) and supplies their lifecycle status + the
 *   namespace Messages uses to subscribe to nested activity.
 */
export function streamMessagesToUi(
  messages: Array<BaseMessage>,
  toolCalls: ReadonlyArray<AssembledToolCall> = [],
  subagents: ReadonlyMap<string, SubagentDiscoverySnapshot> = new Map(),
): Array<Message> {
  const toolCallsById = new Map<string, AssembledToolCall>();
  for (const toolCall of toolCalls) {
    const id = toolCall.id || toolCall.callId;
    if (id) toolCallsById.set(id, toolCall);
  }

  // The discovery map is keyed by subagent name (one entry per name), but each
  // snapshot records the `task` tool-call id that spawned it — so re-index by
  // that id to correlate a snapshot to the exact `task` chunk that created it.
  const subagentsByCallId = new Map<string, SubagentDiscoverySnapshot>();
  for (const snapshot of subagents.values()) {
    if (snapshot.id) subagentsByCallId.set(snapshot.id, snapshot);
  }

  const toolMessagesById = new Map<string, ToolMessage>();
  for (const raw of messages) {
    if (ToolMessage.isInstance(raw) && typeof raw.tool_call_id === "string") {
      toolMessagesById.set(raw.tool_call_id, raw);
    }
  }

  const uiMessages: Array<Message> = [];
  let agentTurn: AgentTurn | null = null;

  const flushAgentTurn = () => {
    if (!agentTurn) return;
    uiMessages.push({ ...agentTurn, chunks: mergeTextChunks(agentTurn.chunks) });
    agentTurn = null;
  };

  const appendAgentChunks = (msgId: string, timestamp: string, chunks: Array<Chunk>) => {
    if (!agentTurn) {
      agentTurn = {
        id: msgId,
        author: "agent",
        timestamp,
        chunks: [...chunks],
      };
    } else {
      agentTurn.timestamp = timestamp;
      agentTurn.chunks.push(...chunks);
    }
  };

  messages.forEach((raw, index) => {
    const msgId = typeof raw.id === "string" && raw.id ? raw.id : `msg-${index}`;
    const timestamp = messageTimestamp(raw);

    if (HumanMessage.isInstance(raw)) {
      flushAgentTurn();
      const content = (raw as unknown as { content?: unknown }).content;
      const chunks = imageChunks(content);
      const text = raw.text.trim();
      if (text) chunks.push({ kind: "text", text });
      if (!chunks.length) return;
      uiMessages.push({
        id: msgId,
        author: "user",
        timestamp,
        chunks,
      });
      return;
    }

    if (AIMessage.isInstance(raw)) {
      const chunks: Array<Chunk> = [];
      const reasoning = reasoningText(raw);
      if (reasoning) chunks.push({ kind: "reasoning", text: reasoning });
      const text = raw.text.trim();
      if (text) chunks.push({ kind: "text", text });

      for (const toolCall of raw.tool_calls ?? []) {
        const name = toolCall.name || "tool";
        if (INTERNAL_TOOLS.has(name)) continue;
        const toolCallId = toolCall.id || `tool-${index}-${chunks.length}`;
        const args = parseToolArgs(toolCall.args);
        const assembled = toolCallsById.get(toolCallId);
        const toolMessage = toolMessagesById.get(toolCallId);
        const chunk: ToolExecutionChunk = {
          kind: "tool-execution",
          toolCallId,
          title: toolTitle(name, args),
          toolKind: toolKind(name),
          input: args,
          status: toolStatus(assembled, toolMessage),
        };
        const output = toolOutputText(assembled, toolMessage);
        if (output) chunk.output = output;
        const diffData = diffFromArtifact(toolMessage?.artifact) ?? maybeDiffFromArgs(args);
        if (diffData) chunk.diffData = diffData;
        // When the SDK has discovered the subagent this `task` call spawned, take
        // its namespace (for scoped nested activity) and authoritative status.
        const subagent = subagentsByCallId.get(toolCallId);
        if (subagent) {
          chunk.subagentNamespace = [...subagent.namespace];
          chunk.status = subagentStatus(subagent);
        }
        chunks.push(chunk);
      }

      if (chunks.length) appendAgentChunks(msgId, timestamp, chunks);
    }

    // `ToolMessage`s no longer produce their own chunk — their status/output is
    // attached to the originating tool-call chunk above via `stream.toolCalls`.
  });

  flushAgentTurn();
  return uiMessages;
}
