import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import type { BaseMessage } from "@langchain/core/messages";

import type { Chunk, DiffData, Message, ToolExecutionChunk } from "./types";

const READ_TOOLS = new Set(["read_file", "read", "glob", "grep"]);
const EDIT_TOOLS = new Set(["write_file", "edit_file", "str_replace", "write", "edit", "patch"]);
const EXECUTE_TOOLS = new Set(["execute", "bash", "shell", "run_terminal_cmd"]);
const SEARCH_TOOLS = new Set(["glob", "grep", "web_search", "fetch_url", "search"]);
const INTERNAL_TOOLS = new Set(["confirming_completion", "no_op"]);

type ToolKind = ToolExecutionChunk["toolKind"];

function toolKind(name: string): ToolKind {
  const lowered = name.toLowerCase();
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
 * Client-side mirror of the server's ``state_messages_to_ui`` (see
 * ``agent/dashboard/message_adapter.py``). Converts the SDK's live
 * ``stream.messages`` projection into the dashboard chunk model so the
 * transcript can stream from the SDK directly instead of being merged
 * into the React Query cache by hand.
 */
export function streamMessagesToUi(messages: Array<BaseMessage>): Array<Message> {
  const pendingTools = new Map<string, ToolExecutionChunk>();
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
      const text = raw.text.trim();
      if (text) chunks.push({ kind: "text", text });

      for (const toolCall of raw.tool_calls ?? []) {
        const name = toolCall.name || "tool";
        if (INTERNAL_TOOLS.has(name)) continue;
        const toolCallId = toolCall.id || `tool-${index}-${chunks.length}`;
        const args = parseToolArgs(toolCall.args);
        const chunk: ToolExecutionChunk = {
          kind: "tool-execution",
          toolCallId,
          title: toolTitle(name, args),
          toolKind: toolKind(name),
          input: args,
          status: "in_progress",
        };
        const diffData = maybeDiffFromArgs(args);
        if (diffData) chunk.diffData = diffData;
        chunks.push(chunk);
        pendingTools.set(toolCallId, chunk);
      }

      if (chunks.length) appendAgentChunks(msgId, timestamp, chunks);
      return;
    }

    if (ToolMessage.isInstance(raw)) {
      const toolCallId = raw.tool_call_id;
      if (typeof toolCallId !== "string") return;
      const name = typeof raw.name === "string" ? raw.name : "tool";
      if (INTERNAL_TOOLS.has(name)) {
        pendingTools.delete(toolCallId);
        return;
      }
      const output = raw.text.trim();
      const pending = pendingTools.get(toolCallId);
      if (pending) {
        pending.status = raw.status === "error" ? "error" : "completed";
        if (output) pending.output = output;
        return;
      }
      if (!agentTurn) {
        agentTurn = { id: msgId, author: "agent", timestamp, chunks: [] };
      }
      agentTurn.chunks.push({
        kind: "tool-execution",
        toolCallId,
        title: name,
        toolKind: toolKind(name),
        status: "completed",
        output,
      });
    }
  });

  flushAgentTurn();
  return uiMessages;
}
