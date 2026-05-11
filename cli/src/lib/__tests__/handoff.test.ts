import { describe, it, expect } from 'vitest';
import { validateBundle, MAX_BUNDLE_BYTES } from '@lib/handoff';

const goodBundle = () => ({
  thread_id: 't',
  source: 'local' as const,
  taken_at: '2025-01-01T00:00:00Z',
  conversation: [],
  pending_queue: [],
  git: {
    remote_url: 'https://github.com/o/r.git',
    branch: 'main',
    head_sha: 'abc',
    uncommitted_diff: '',
    untracked_files: [],
  },
  agent: {},
});

describe('validateBundle', () => {
  it('accepts a well-formed bundle', () => {
    const r = validateBundle(goodBundle());
    expect(r.ok).toBe(true);
  });

  it('rejects missing source', () => {
    const b = { ...goodBundle(), source: 'nope' };
    const r = validateBundle(b);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toMatch(/source/);
  });

  it('rejects missing git fields', () => {
    const b = goodBundle();
    (b.git as { head_sha: string }).head_sha = '';
    const r = validateBundle(b);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toMatch(/head_sha/);
  });

  it('rejects oversized bundle', () => {
    const b = goodBundle();
    b.git.uncommitted_diff = 'x'.repeat(MAX_BUNDLE_BYTES + 1);
    const r = validateBundle(b);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toMatch(/exceeds/);
  });
});
