import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import { messageArrivalTimestamp } from "./messageTimestamps";
import { humanizeToolName } from "./toolNames";
import type { BaseMessage, ContentBlock } from "@langchain/core/messages";
import type { AssembledToolCall, SubagentDiscoverySnapshot } from "@langchain/react";

import type { Chunk, DiffData, Message, SubagentInfo, ToolExecutionChunk } from "./types";

const READ_TOOLS = new Set(["read_file", "read", "ls"]);
const EDIT_TOOLS = new Set(["write_file", "edit_file", "str_replace", "write", "edit", "patch"]);
const EXECUTE_TOOLS = new Set(["execute", "bash", "shell", "run_terminal_cmd"]);
const SEARCH_TOOLS = new Set(["glob", "grep", "web_search", "search"]);
const FETCH_TOOLS = new Set(["fetch", "fetch_url", "http_request"]);
const INTERNAL_TOOLS = new Set(["confirming_completion", "no_op"]);

type ToolKind = ToolExecutionChunk["toolKind"];

function toolKind(name: string): ToolKind {
  const lowered = name.toLowerCase();
  // deepagents' subagent spawner — surfaced as a subagent card in Messages.
  if (lowered === "task") return "task";
  if (lowered === "slack_thread_reply") return "slack";
  if (lowered === "linear_comment") return "linear";
  if (lowered === "write_todos") return "other";
  if (EDIT_TOOLS.has(lowered) || ["edit", "write", "replace"].some((t) => lowered.includes(t))) {
    return "edit";
  }
  if (EXECUTE_TOOLS.has(lowered)) return "execute";
  if (FETCH_TOOLS.has(lowered)) return "fetch";
  if (SEARCH_TOOLS.has(lowered)) return "search";
  if (READ_TOOLS.has(lowered) || lowered.includes("read")) return "read";
  if (lowered === "think") return "think";
  return "other";
}

export function toolTitle(name: string, args: Record<string, unknown>): string {
  const path = args.path ?? args.file_path ?? args.target_file;
  if (typeof path === "string" && path.trim()) return `${name} ${path.trim()}`;
  const command = args.command;
  if (typeof command === "string" && command.trim()) {
    return command.trim().split("\n")[0]?.slice(0, 120) ?? "";
  }
  return humanizeToolName(name);
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

type AgentTurn = {
  id: string;
  author: Message["author"];
  timestamp: string;
  turnKey?: string;
  startedAt: string;
  timestampIsFallback?: boolean;
  chunks: Array<Chunk>;
};

type MessageTimestamp = {
  value: string;
  isFallback: boolean;
};

function messageTimestamp(
  raw: BaseMessage,
  msgId: string,
  resolveCreatedAt?: (messageId: string) => string | undefined,
): MessageTimestamp {
  const msg = raw as unknown as Record<string, unknown>;
  const createdAt = msg.created_at;
  if (typeof createdAt === "string" && createdAt) {
    return { value: createdAt, isFallback: false };
  }
  const responseMetadata = msg.response_metadata;
  if (responseMetadata && typeof responseMetadata === "object") {
    const metadataCreatedAt = (responseMetadata as Record<string, unknown>).created_at;
    if (typeof metadataCreatedAt === "string" && metadataCreatedAt) {
      return { value: metadataCreatedAt, isFallback: false };
    }
  }
  const resolved = resolveCreatedAt?.(msgId);
  if (typeof resolved === "string" && resolved) {
    return { value: resolved, isFallback: true };
  }
  return { value: new Date().toISOString(), isFallback: true };
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

/** Flatten a {@link SubagentDiscoverySnapshot} onto the chunk model. */
function subagentInfo(snapshot: SubagentDiscoverySnapshot): SubagentInfo {
  const info: SubagentInfo = {
    namespace: [...snapshot.namespace],
    name: snapshot.name,
    depth: snapshot.depth,
    parentId: snapshot.parentId,
  };
  const startedAt = isoOrUndefined(snapshot.startedAt);
  if (startedAt) info.startedAt = startedAt;
  const completedAt = isoOrUndefined(snapshot.completedAt);
  if (completedAt) info.completedAt = completedAt;
  const result = subagentResultText(snapshot.output);
  if (result) info.result = result;
  const error = snapshot.error?.trim();
  if (error) info.error = error;
  return info;
}

function isoOrUndefined(value: Date | null | undefined): string | undefined {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) return undefined;
  return value.toISOString();
}

/**
 * The subagent's answer, from the discovery snapshot's `output`.
 *
 * Usually the raw `ToolMessage` the parent received rather than a bare string,
 * so unwrap `content` — otherwise the card renders the envelope
 * (`{"type":"tool","content":…,"tool_call_id":…}`) instead of the answer.
 * Serialized LangChain messages nest their fields under `kwargs`/`lc_kwargs`.
 */
function subagentResultText(output: unknown): string | undefined {
  if (output == null) return undefined;
  if (typeof output === "string") return output.trim() || undefined;
  const content = messageContent(output);
  if (content != null) return content.trim() || undefined;
  try {
    return JSON.stringify(output);
  } catch {
    return String(output);
  }
}

function messageContent(value: unknown): string | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  for (const source of [record, record.kwargs, record.lc_kwargs]) {
    if (!source || typeof source !== "object") continue;
    const content = (source as Record<string, unknown>).content;
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      return content
        .map((block) =>
          block && typeof block === "object" && "text" in block && typeof block.text === "string"
            ? block.text
            : "",
        )
        .join("");
    }
  }
  return undefined;
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
 * Convert the SDK's live projections into the dashboard chunk model so the
 * transcript streams (and hydrates) directly from the SDK instead of a
 * hand-rolled, server-mirrored adapter.
 *
 * - `messages` ({@link BaseMessage}[]) drives ordering, text, and reasoning.
 * - `toolCalls` ({@link AssembledToolCall}[], i.e. `stream.toolCalls`) drives
 *   each tool call's status and output — no `pendingTools` bookkeeping.
 * - `toolKind` / `title` stay a pure mapping of name+args (known at call time,
 *   already persisted) so the in-progress card renders instantly.
 * - `diffData` is derived from the call's own args, which is all that is
 *   available before an edit is applied (what a pending approval renders). What
 *   a turn actually changed comes from git, via the turn-diff endpoint.
 * - `subagents` ({@link SubagentDiscoverySnapshot}[], i.e. `stream.subagents`)
 *   authoritatively identifies `task` calls that spawned a subagent (matched by
 *   `snapshot.id === toolCallId`) and supplies their lifecycle status + the
 *   namespace Messages uses to subscribe to nested activity.
 */
export function streamMessagesToUi(
  messages: Array<BaseMessage>,
  toolCalls: ReadonlyArray<AssembledToolCall> = [],
  subagents: ReadonlyMap<string, SubagentDiscoverySnapshot> = new Map(),
  resolveCreatedAt?: (messageId: string) => string | undefined,
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
  let turnKey: string | undefined;

  const flushAgentTurn = () => {
    if (!agentTurn) return;
    uiMessages.push({ ...agentTurn, chunks: mergeTextChunks(agentTurn.chunks) });
    agentTurn = null;
  };

  const appendAgentChunks = (
    msgId: string,
    timestamp: string,
    timestampIsFallback: boolean,
    chunks: Array<Chunk>,
  ) => {
    if (!agentTurn) {
      agentTurn = {
        id: msgId,
        author: "agent",
        timestamp,
        turnKey,
        startedAt: timestamp,
        timestampIsFallback,
        chunks: [...chunks],
      };
    } else {
      agentTurn.timestamp = timestamp;
      agentTurn.timestampIsFallback =
        agentTurn.timestampIsFallback || timestampIsFallback;
      agentTurn.chunks.push(...chunks);
    }
  };

  messages.forEach((raw, index) => {
    const msgId = typeof raw.id === "string" && raw.id ? raw.id : `msg-${index}`;
    const { value: timestamp, isFallback: timestampIsFallback } =
      messageTimestamp(raw, msgId, resolveCreatedAt);

    if (HumanMessage.isInstance(raw)) {
      flushAgentTurn();
      turnKey = typeof raw.id === "string" ? raw.id : undefined;
      const content = (raw as unknown as { content?: unknown }).content;
      const chunks = imageChunks(content);
      const text = raw.text.trim();
      if (text) chunks.push({ kind: "text", text });
      if (!chunks.length) return;
      uiMessages.push({
        id: msgId,
        author: "user",
        timestamp,
        timestampIsFallback,
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
          timestamp: messageArrivalTimestamp(toolCallId),
          title: toolTitle(name, args),
          toolKind: toolKind(name),
          input: args,
          status: toolStatus(assembled, toolMessage),
        };
        const output = toolOutputText(assembled, toolMessage);
        if (output) chunk.output = output;
        const diffData = maybeDiffFromArgs(args);
        if (diffData) chunk.diffData = diffData;
        // When the SDK has discovered the subagent this `task` call spawned, take
        // its namespace (for scoped nested activity) and authoritative status.
        const subagent = subagentsByCallId.get(toolCallId);
        if (subagent) {
          chunk.subagent = subagentInfo(subagent);
          chunk.status = subagentStatus(subagent);
        }
        chunks.push(chunk);
      }

      if (chunks.length) {
        appendAgentChunks(msgId, timestamp, timestampIsFallback, chunks);
      }
    }

    // `ToolMessage`s no longer produce their own chunk — their status/output is
    // attached to the originating tool-call chunk above via `stream.toolCalls`.
  });

  flushAgentTurn();
  return uiMessages;
}
