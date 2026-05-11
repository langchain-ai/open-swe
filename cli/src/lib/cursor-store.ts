// Per-thread SSE replay cursor persistence.
//
// DESIGN.md §"the attach view": "Reattaching re-opens the stream; the LangGraph
// SDK supports replaying from a cursor, so the CLI passes a `?since=<event_id>`
// query param to backfill missed events." We persist the last observed event
// id under ~/.openswe/cursors/<thread_id>.json so the cursor survives detach,
// `openswe` restarts, and reattach via `openswe attach <id>`.

import fs from 'fs/promises';
import os from 'os';
import path from 'path';

function cursorDir(): string {
  return path.join(os.homedir(), '.openswe', 'cursors');
}

function cursorPath(thread_id: string): string {
  // thread_id is a UUID; no path traversal concern, but encode defensively.
  const safe = thread_id.replace(/[^a-zA-Z0-9_.-]/g, '_');
  return path.join(cursorDir(), `${safe}.json`);
}

export async function loadCursor(thread_id: string): Promise<string | null> {
  try {
    const raw = await fs.readFile(cursorPath(thread_id), 'utf-8');
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      typeof (parsed as { since?: unknown }).since === 'string'
    ) {
      return (parsed as { since: string }).since;
    }
    return null;
  } catch {
    return null;
  }
}

export async function saveCursor(thread_id: string, since: string): Promise<void> {
  try {
    await fs.mkdir(cursorDir(), { recursive: true });
    await fs.writeFile(
      cursorPath(thread_id),
      JSON.stringify({ since, saved_at: new Date().toISOString() }),
      'utf-8',
    );
  } catch {
    // Cursor persistence is best-effort — don't surface IO errors to the user.
  }
}

export async function clearCursor(thread_id: string): Promise<void> {
  try {
    await fs.unlink(cursorPath(thread_id));
  } catch {
    // ignore
  }
}
