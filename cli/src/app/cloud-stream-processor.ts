import { processStreamUpdate } from '@app/stream-processor.js';
import type { BaseMessage } from '@langchain/core/messages';
import type { StreamProcessorActions } from '@types';
import type { SSEEvent } from '@lib/api-types';

/**
 * Translate one SSE event from /cli/runs/{id}/stream into store updates,
 * reusing the same processStreamUpdate contract as the local agent.
 *
 * The backend forwards LangGraph SDK events; events with mode === "updates"
 * carry the per-node `Record<string, { messages: BaseMessage[] }>` chunk
 * that processStreamUpdate already understands. Other event types (metadata,
 * errors, end-of-stream) are returned via `meta` so the caller can react.
 */
export type CloudStreamMeta =
  | { kind: 'error'; message: string }
  | { kind: 'end' }
  | { kind: 'metadata'; data: Record<string, unknown> }
  | { kind: 'event-timestamp'; iso: string }
  | { kind: 'ignored' };

export async function processCloudEvent(
  ev: SSEEvent,
  conversationHistory: { current: BaseMessage[] },
  actions: StreamProcessorActions,
): Promise<CloudStreamMeta> {
  const eventName = (ev.event ?? 'message').toLowerCase();
  const data = ev.data;

  if (eventName === 'error') {
    const message =
      typeof data === 'object' && data !== null && typeof (data as { message?: unknown }).message === 'string'
        ? (data as { message: string }).message
        : typeof data === 'string'
        ? data
        : 'Cloud stream error';
    return { kind: 'error', message };
  }

  if (eventName === 'end' || eventName === 'complete' || eventName === 'done') {
    return { kind: 'end' };
  }

  if (eventName === 'metadata') {
    if (typeof data === 'object' && data !== null) {
      return { kind: 'metadata', data: data as Record<string, unknown> };
    }
    return { kind: 'ignored' };
  }

  // Default: assume LangGraph "updates" mode chunk.
  if (data && typeof data === 'object') {
    const record = data as Record<string, unknown>;
    // Some SSE wrappers nest the chunk under `chunk` or `update`.
    const chunkLike =
      'chunk' in record && typeof record.chunk === 'object' && record.chunk !== null
        ? (record.chunk as Record<string, unknown>)
        : 'update' in record && typeof record.update === 'object' && record.update !== null
        ? (record.update as Record<string, unknown>)
        : record;
    await processStreamUpdate(chunkLike as Record<string, any>, conversationHistory, actions);

    const ts = extractTimestamp(record);
    if (ts) return { kind: 'event-timestamp', iso: ts };
  }

  return { kind: 'ignored' };
}

function extractTimestamp(record: Record<string, unknown>): string | null {
  const candidates = ['timestamp', 'created_at', 'ts', 'last_event_at'];
  for (const k of candidates) {
    const v = record[k];
    if (typeof v === 'string') return v;
  }
  return null;
}
