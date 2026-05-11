import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import os from 'os';
import path from 'path';
import fs from 'fs/promises';
import { loadCursor, saveCursor, clearCursor } from '@lib/cursor-store';

let tmpHome: string;
let originalHome: string | undefined;

beforeEach(async () => {
  tmpHome = await fs.mkdtemp(path.join(os.tmpdir(), 'openswe-cursor-test-'));
  originalHome = process.env.HOME;
  // The module uses os.homedir() which honors HOME on POSIX.
  process.env.HOME = tmpHome;
  vi.spyOn(os, 'homedir').mockReturnValue(tmpHome);
});

afterEach(async () => {
  vi.restoreAllMocks();
  if (originalHome === undefined) delete process.env.HOME;
  else process.env.HOME = originalHome;
  await fs.rm(tmpHome, { recursive: true, force: true });
});

describe('cursor-store', () => {
  it('returns null when no cursor exists', async () => {
    expect(await loadCursor('thread-abc')).toBeNull();
  });

  it('round-trips a saved cursor', async () => {
    await saveCursor('thread-abc', 'event-42');
    expect(await loadCursor('thread-abc')).toBe('event-42');
  });

  it('clears a saved cursor', async () => {
    await saveCursor('thread-abc', 'event-42');
    await clearCursor('thread-abc');
    expect(await loadCursor('thread-abc')).toBeNull();
  });

  it('isolates cursors per thread_id', async () => {
    await saveCursor('a', 'e-a');
    await saveCursor('b', 'e-b');
    expect(await loadCursor('a')).toBe('e-a');
    expect(await loadCursor('b')).toBe('e-b');
  });

  it('returns null on malformed file', async () => {
    await fs.mkdir(path.join(tmpHome, '.openswe', 'cursors'), { recursive: true });
    await fs.writeFile(path.join(tmpHome, '.openswe', 'cursors', 'thread-abc.json'), 'not json');
    expect(await loadCursor('thread-abc')).toBeNull();
  });
});
