import type { SSEEvent } from '@lib/api-types';

type ConsumeOptions = {
  headers?: Record<string, string>;
  signal?: AbortSignal;
  reconnect?: boolean;
};

type ParsedEvent = {
  event?: string;
  data: string;
  id?: string;
};

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30_000;

function appendSince(url: string, since: string): string {
  const u = new URL(url);
  u.searchParams.set('since', since);
  return u.toString();
}

async function* parseStream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<ParsedEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      if (signal?.aborted) return;
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sepIndex: number;
      while ((sepIndex = findSeparator(buffer)) !== -1) {
        const rawEvent = buffer.slice(0, sepIndex);
        buffer = buffer.slice(sepIndex + (buffer.startsWith('\r\n', sepIndex) ? 2 : separatorLen(buffer, sepIndex)));
        const parsed = parseEvent(rawEvent);
        if (parsed) yield parsed;
      }
    }
    if (buffer.trim().length > 0) {
      const parsed = parseEvent(buffer);
      if (parsed) yield parsed;
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // ignore
    }
  }
}

function findSeparator(buf: string): number {
  const a = buf.indexOf('\n\n');
  const b = buf.indexOf('\r\n\r\n');
  if (a === -1) return b;
  if (b === -1) return a;
  return Math.min(a, b);
}

function separatorLen(buf: string, idx: number): number {
  return buf.startsWith('\r\n\r\n', idx) ? 4 : 2;
}

function parseEvent(raw: string): ParsedEvent | null {
  const lines = raw.split(/\r?\n/);
  let event: string | undefined;
  let id: string | undefined;
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.length === 0) continue;
    if (line.startsWith(':')) continue; // comment
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    switch (field) {
      case 'event':
        event = value;
        break;
      case 'data':
        dataLines.push(value);
        break;
      case 'id':
        id = value;
        break;
      default:
        break;
    }
  }
  if (dataLines.length === 0 && event === undefined && id === undefined) return null;
  return { event, id, data: dataLines.join('\n') };
}

export async function* consumeSSE(
  url: string,
  opts: ConsumeOptions = {},
): AsyncIterable<SSEEvent> {
  const reconnect = opts.reconnect !== false;
  let currentUrl = url;
  let lastId: string | undefined;
  let backoff = INITIAL_BACKOFF_MS;

  while (true) {
    if (opts.signal?.aborted) return;
    let response: Response;
    try {
      response = await fetch(currentUrl, {
        headers: {
          Accept: 'text/event-stream',
          'Cache-Control': 'no-cache',
          ...(opts.headers ?? {}),
        },
        signal: opts.signal,
      });
    } catch (err) {
      if (opts.signal?.aborted) return;
      if (!reconnect) throw err;
      await sleep(backoff, opts.signal);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
      continue;
    }

    if (!response.ok || !response.body) {
      if (!reconnect) {
        throw new Error(`SSE stream failed: ${response.status} ${response.statusText}`);
      }
      await sleep(backoff, opts.signal);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
      continue;
    }

    backoff = INITIAL_BACKOFF_MS;

    try {
      for await (const ev of parseStream(response.body, opts.signal)) {
        if (ev.id) lastId = ev.id;
        let data: unknown = ev.data;
        if (ev.data.length > 0) {
          try {
            data = JSON.parse(ev.data);
          } catch {
            data = ev.data;
          }
        }
        yield { event: ev.event, id: ev.id, data };
      }
    } catch (err) {
      if (opts.signal?.aborted) return;
      if (!reconnect) throw err;
    }

    if (opts.signal?.aborted) return;
    if (!reconnect) return;

    if (lastId) currentUrl = appendSince(url, lastId);
    await sleep(backoff, opts.signal);
    backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
  }
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      resolve();
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}
