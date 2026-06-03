import { useEffect } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

import { agentsApi } from "./api";
import { agentThreadKeys } from "./queries";
import type { AgentThread, Message, TextChunk } from "./types";

interface StreamPart {
  event?: string;
  data?: unknown;
  id?: string | null;
}

interface ChunkLike {
  id?: string;
  type?: string;
  content?: unknown;
}

function extractText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    let out = "";
    for (const part of content) {
      if (typeof part === "string") {
        out += part;
      } else if (part && typeof part === "object" && "text" in part) {
        const text = (part as { text?: unknown }).text;
        if (typeof text === "string") out += text;
      }
    }
    return out;
  }
  return "";
}

function appendTokenChunk(thread: AgentThread, chunk: ChunkLike): AgentThread {
  const text = extractText(chunk.content);
  if (!text || !chunk.id) return thread;
  const id = chunk.id;

  const existing = thread.messages.find((m) => m.id === id);
  if (existing) {
    const chunks = existing.chunks.slice();
    const textIdx = chunks.findIndex((c) => c.kind === "text");
    if (textIdx >= 0) {
      const prev = chunks[textIdx] as TextChunk;
      chunks[textIdx] = { ...prev, text: prev.text + text };
    } else {
      chunks.unshift({ kind: "text", text });
    }
    const updated: Message = { ...existing, chunks };
    return {
      ...thread,
      messages: thread.messages.map((m) => (m.id === id ? updated : m)),
    };
  }

  const newMessage: Message = {
    id,
    author: "agent",
    timestamp: new Date().toISOString(),
    chunks: [{ kind: "text", text }],
  };
  return { ...thread, messages: [...thread.messages, newMessage] };
}

function isStreamingChunk(chunk: ChunkLike): boolean {
  const type = chunk.type;
  if (typeof type !== "string") return false;
  return type === "AIMessageChunk" || type.endsWith("MessageChunk");
}

function applyMessagesEvent(
  threadId: string,
  data: unknown,
  queryClient: QueryClient,
): boolean {
  if (!Array.isArray(data) || data.length === 0) return false;
  const first = data[0];
  if (!first || typeof first !== "object") return false;
  const chunk = first as ChunkLike;
  if (!isStreamingChunk(chunk)) return false;

  const key = agentThreadKeys.detail(threadId);
  void queryClient.cancelQueries({ queryKey: key });
  queryClient.setQueryData<AgentThread | undefined>(
    key,
    (prev) => (prev ? appendTokenChunk(prev, chunk) : prev),
  );
  return true;
}

function handleStreamPart(
  threadId: string,
  part: StreamPart,
  queryClient: QueryClient,
) {
  const event = part.event ?? "";
  if (event.startsWith("messages")) {
    const handled = applyMessagesEvent(threadId, part.data, queryClient);
    if (handled) return;
  }
  queryClient.invalidateQueries({ queryKey: agentThreadKeys.detail(threadId) });
}

export function useAgentThreadStream(threadId: string, enabled: boolean) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled) return;

    const controller = new AbortController();
    let cancelled = false;

    async function consume() {
      try {
        const res = await fetch(agentsApi.streamUrl(threadId), {
          credentials: "include",
          signal: controller.signal,
          headers: { Accept: "text/event-stream" },
        });
        if (!res.ok || !res.body) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (!cancelled) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let boundary = buffer.indexOf("\n\n");
          while (boundary !== -1) {
            const block = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            for (const line of block.split("\n")) {
              if (!line.startsWith("data: ")) continue;
              const raw = line.slice(6);
              try {
                const part = JSON.parse(raw) as StreamPart;
                handleStreamPart(threadId, part, queryClient);
              } catch {
                queryClient.invalidateQueries({
                  queryKey: agentThreadKeys.detail(threadId),
                });
              }
            }
            boundary = buffer.indexOf("\n\n");
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          console.debug("agent thread stream closed", error);
        }
      }
    }

    void consume();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [enabled, queryClient, threadId]);
}
