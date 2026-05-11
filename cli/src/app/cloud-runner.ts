import type { BaseMessage } from '@langchain/core/messages';
import { ApiClient } from '@lib/api-client';
import { consumeSSE } from '@lib/sse';
import { processCloudEvent } from '@app/cloud-stream-processor.js';
import { logError } from '@lib/logger';
import type { RunSource } from '@lib/api-types';
import type { Message, StreamProcessorActions, ToolExecution, TokenUsage } from '@types';

export type CloudRunnerStatus =
  | { kind: 'connecting' }
  | { kind: 'connected'; source?: RunSource; repo?: string; branch?: string }
  | { kind: 'event'; event_time?: number }
  | { kind: 'closed' }
  | { kind: 'error'; message: string };

export type CloudRunnerDeps = {
  addMessage: (message: Omit<Message, 'id'>) => void;
  updateToolExecution: (toolExecution: ToolExecution) => void;
  updateTokenUsage: (usage: TokenUsage) => void;
  setBusy: (busy: boolean) => void;
  onStatus?: (status: CloudRunnerStatus) => void;
};

export class CloudRunner {
  private api: ApiClient;
  private thread_id: string;
  private deps: CloudRunnerDeps;
  private abortController?: AbortController;
  private conversation: { current: BaseMessage[] } = { current: [] };
  private lastEventIso: string | null = null;
  private connected = false;

  constructor(api: ApiClient, thread_id: string, deps: CloudRunnerDeps) {
    this.api = api;
    this.thread_id = thread_id;
    this.deps = deps;
  }

  private emit(status: CloudRunnerStatus): void {
    try {
      this.deps.onStatus?.(status);
    } catch (err) {
      void logError(`CloudRunner onStatus threw: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  async attach(opts: { since?: string | null } = {}): Promise<void> {
    if (this.abortController) {
      throw new Error('CloudRunner is already attached');
    }
    this.abortController = new AbortController();
    const signal = this.abortController.signal;
    // Seed the resume cursor before opening the stream. Without this, an
    // `openswe attach <id>` after a prior detach replays from the start of
    // the run instead of backfilling missed events (DESIGN.md §attach view).
    if (opts.since) {
      this.lastEventIso = opts.since;
    }
    const url = this.api.streamUrl(this.thread_id, opts.since ?? undefined);

    this.emit({ kind: 'connecting' });
    this.deps.setBusy(true);

    const actions: StreamProcessorActions = {
      addMessage: this.deps.addMessage,
      updateToolExecution: this.deps.updateToolExecution,
      updateTokenUsage: this.deps.updateTokenUsage,
    };

    try {
      for await (const ev of consumeSSE(url, {
        headers: this.api.authHeaders(),
        signal,
      })) {
        if (!this.connected) {
          this.connected = true;
          const meta = extractConnectionMeta(ev.data);
          this.emit({ kind: 'connected', ...meta });
        }
        const result = await processCloudEvent(ev, this.conversation, actions);
        switch (result.kind) {
          case 'error':
            this.deps.addMessage({
              author: 'system',
              chunks: [{ kind: 'error', text: result.message }],
            });
            this.emit({ kind: 'error', message: result.message });
            break;
          case 'end':
            this.deps.setBusy(false);
            break;
          case 'event-timestamp': {
            const ms = Date.parse(result.iso);
            this.lastEventIso = result.iso;
            this.emit({ kind: 'event', event_time: Number.isNaN(ms) ? undefined : ms });
            break;
          }
          default:
            this.emit({ kind: 'event' });
            break;
        }
      }
      this.emit({ kind: 'closed' });
    } catch (err) {
      if (!signal.aborted) {
        const message = err instanceof Error ? err.message : String(err);
        await logError(`Cloud stream error: ${message}`);
        this.deps.addMessage({ author: 'system', chunks: [{ kind: 'error', text: `Cloud stream error: ${message}` }] });
        this.emit({ kind: 'error', message });
      } else {
        this.emit({ kind: 'closed' });
      }
    } finally {
      this.deps.setBusy(false);
    }
  }

  async sendMessage(content: string): Promise<void> {
    const { queued_at } = await this.api.sendMessage(this.thread_id, content);
    // No optimistic addMessage here — Attach.tsx already echoes the user
    // message before calling sendMessage. We expose queued_at via the
    // returned promise so the caller can show a "queued" badge.
    void queued_at;
  }

  async interrupt(): Promise<void> {
    try {
      await this.api.interrupt(this.thread_id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      await logError(`Failed to interrupt: ${message}`);
      this.deps.addMessage({ author: 'system', chunks: [{ kind: 'error', text: `Failed to interrupt: ${message}` }] });
    }
  }

  detach(): void {
    this.abortController?.abort();
    this.abortController = undefined;
  }

  getLastEventIso(): string | null {
    return this.lastEventIso;
  }
}

function extractConnectionMeta(
  data: unknown,
): { source?: RunSource; repo?: string; branch?: string } {
  if (typeof data !== 'object' || data === null) return {};
  const r = data as Record<string, unknown>;
  const out: { source?: RunSource; repo?: string; branch?: string } = {};
  const src = r.source;
  if (src === 'github' || src === 'slack' || src === 'linear' || src === 'cli') {
    out.source = src;
  }
  if (typeof r.repo === 'string') out.repo = r.repo;
  if (typeof r.branch === 'string') out.branch = r.branch;
  return out;
}
