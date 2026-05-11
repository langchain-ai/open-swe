// Local↔cloud handoff bundle pack/unpack.
//
// The envelope shape matches what the backend produces in
// agent/utils/handoff.py — see cli/DESIGN.md "Handoff" for the spec.

import { spawn, type SpawnOptions } from 'node:child_process';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';

export const MAX_BUNDLE_BYTES = 5 * 1024 * 1024;
export const MAX_UNTRACKED_FILE_BYTES = 256 * 1024;

export type UntrackedFile = {
  path: string;
  content?: string;
  encoding?: 'utf-8' | 'base64';
  skipped?: boolean;
  reason?: string;
};

export type HandoffGit = {
  remote_url: string;
  branch: string;
  head_sha: string;
  uncommitted_diff: string;
  untracked_files: UntrackedFile[];
};

export type HandoffBundle = {
  thread_id?: string;
  source: 'local' | 'cloud';
  taken_at: string;
  conversation: unknown[];
  pending_queue: unknown[];
  git: HandoffGit;
  agent: Record<string, unknown>;
};

type RunResult = { code: number | null; stdout: string; stderr: string };

const runGit = async (args: string[], cwd: string): Promise<RunResult> => {
  return runCommand('git', args, { cwd });
};

const runCommand = async (
  cmd: string,
  args: string[],
  opts: SpawnOptions & { cwd: string },
): Promise<RunResult> => {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { ...opts, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout?.on('data', (d) => {
      stdout += d.toString();
    });
    child.stderr?.on('data', (d) => {
      stderr += d.toString();
    });
    child.on('close', (code) => resolve({ code, stdout, stderr }));
    child.on('error', () => resolve({ code: -1, stdout, stderr }));
  });
};

const runCommandWithStdin = async (
  cmd: string,
  args: string[],
  stdin: string,
  opts: SpawnOptions & { cwd: string },
): Promise<RunResult> => {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { ...opts, stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout?.on('data', (d) => {
      stdout += d.toString();
    });
    child.stderr?.on('data', (d) => {
      stderr += d.toString();
    });
    child.on('close', (code) => resolve({ code, stdout, stderr }));
    child.on('error', () => resolve({ code: -1, stdout, stderr }));
    child.stdin?.write(stdin);
    child.stdin?.end();
  });
};

export type LocalSnapshot = {
  conversation: unknown[];
  pending_queue?: unknown[];
  agent?: Record<string, unknown>;
};

export type ExportOpts = {
  thread_id?: string;
  workdir?: string;
};

export const exportLocal = async (
  snapshot: LocalSnapshot,
  opts: ExportOpts = {},
): Promise<HandoffBundle> => {
  const workdir = opts.workdir ?? process.cwd();

  const remote = await runGit(['remote', 'get-url', 'origin'], workdir);
  if (remote.code !== 0) {
    throw new Error(`Could not read git remote 'origin' in ${workdir}: ${remote.stderr.trim()}`);
  }
  const branch = await runGit(['rev-parse', '--abbrev-ref', 'HEAD'], workdir);
  const head = await runGit(['rev-parse', 'HEAD'], workdir);
  if (head.code !== 0) {
    throw new Error(`Could not read HEAD in ${workdir}: ${head.stderr.trim()}`);
  }
  const diff = await runGit(['diff', 'HEAD'], workdir);
  const untracked = await runGit(['ls-files', '--others', '--exclude-standard'], workdir);

  const untracked_files: UntrackedFile[] = [];
  for (const rel of untracked.stdout.split('\n')) {
    const rp = rel.trim();
    if (!rp) continue;
    try {
      const full = path.join(workdir, rp);
      const stat = await fs.stat(full);
      if (stat.size > MAX_UNTRACKED_FILE_BYTES) {
        untracked_files.push({ path: rp, skipped: true, reason: `file too large (${stat.size} bytes)` });
        continue;
      }
      const buf = await fs.readFile(full);
      try {
        const text = buf.toString('utf-8');
        // Heuristic: if the round-trip encodes back identically and has no NUL,
        // treat as text.
        if (!buf.includes(0) && Buffer.from(text, 'utf-8').equals(buf)) {
          untracked_files.push({ path: rp, content: text, encoding: 'utf-8' });
        } else {
          untracked_files.push({ path: rp, content: buf.toString('base64'), encoding: 'base64' });
        }
      } catch {
        untracked_files.push({ path: rp, content: buf.toString('base64'), encoding: 'base64' });
      }
    } catch {
      // file disappeared between ls and read; skip silently
    }
  }

  const bundle: HandoffBundle = {
    thread_id: opts.thread_id,
    source: 'local',
    taken_at: new Date().toISOString(),
    conversation: snapshot.conversation,
    pending_queue: snapshot.pending_queue ?? [],
    git: {
      remote_url: remote.stdout.trim(),
      branch: branch.stdout.trim(),
      head_sha: head.stdout.trim(),
      uncommitted_diff: diff.stdout,
      untracked_files,
    },
    agent: snapshot.agent ?? {},
  };

  const v = validateBundle(bundle);
  if (!v.ok) {
    throw new Error(`Bundle would be invalid: ${v.error}`);
  }
  return bundle;
};

export type ValidateResult = { ok: true } | { ok: false; error: string };

export const validateBundle = (bundle: unknown): ValidateResult => {
  if (!bundle || typeof bundle !== 'object') return { ok: false, error: 'Bundle must be an object' };
  const b = bundle as Record<string, unknown>;
  if (b.source !== 'local' && b.source !== 'cloud') {
    return { ok: false, error: "source must be 'local' or 'cloud'" };
  }
  if (!Array.isArray(b.conversation)) return { ok: false, error: 'conversation must be a list' };
  const git = b.git;
  if (!git || typeof git !== 'object') return { ok: false, error: 'git must be an object' };
  const g = git as Record<string, unknown>;
  for (const k of ['remote_url', 'branch', 'head_sha'] as const) {
    const v = g[k];
    if (typeof v !== 'string' || !v) return { ok: false, error: `git.${k} is required` };
  }
  const size = Buffer.byteLength(JSON.stringify(bundle), 'utf-8');
  if (size > MAX_BUNDLE_BYTES) {
    return { ok: false, error: `bundle exceeds ${MAX_BUNDLE_BYTES} bytes (${size})` };
  }
  return { ok: true };
};

const normalizeRemoteForCompare = (remote: string): string => {
  let r = remote.trim();
  if (r.endsWith('.git')) r = r.slice(0, -4);
  // Normalize git@github.com:owner/repo → github.com/owner/repo
  if (r.startsWith('git@')) {
    r = r.replace(/^git@([^:]+):/, '$1/');
  } else {
    r = r.replace(/^(https?|ssh|git):\/\/(?:git@)?/, '');
  }
  return r.toLowerCase();
};

export const applyToLocal = async (
  bundle: HandoffBundle,
  workdir: string,
): Promise<void> => {
  const v = validateBundle(bundle);
  if (!v.ok) throw new Error(`Invalid bundle: ${v.error}`);

  // Verify it's a git repo.
  const inRepo = await runGit(['rev-parse', '--is-inside-work-tree'], workdir);
  if (inRepo.code !== 0 || inRepo.stdout.trim() !== 'true') {
    throw new Error(`${workdir} is not inside a git working tree`);
  }

  // Verify same repo (origin remote matches).
  const remote = await runGit(['remote', 'get-url', 'origin'], workdir);
  if (remote.code !== 0) {
    throw new Error(`Could not read git remote 'origin' in ${workdir}`);
  }
  if (normalizeRemoteForCompare(remote.stdout) !== normalizeRemoteForCompare(bundle.git.remote_url)) {
    throw new Error(
      `Repo mismatch: local origin is ${remote.stdout.trim()}, bundle expects ${bundle.git.remote_url}`,
    );
  }

  // Verify clean working tree.
  const status = await runGit(['status', '--porcelain'], workdir);
  if (status.code !== 0) throw new Error(`git status failed: ${status.stderr.trim()}`);
  if (status.stdout.trim().length > 0) {
    throw new Error(
      `Working tree is not clean. Commit or stash changes in ${workdir} before applying handoff.`,
    );
  }

  // Fetch (best-effort) to make sure head_sha is available, then check it out.
  await runGit(['fetch', '--all', '--quiet'], workdir);
  const checkout = await runGit(['checkout', '--detach', bundle.git.head_sha], workdir);
  if (checkout.code !== 0) {
    throw new Error(`git checkout ${bundle.git.head_sha} failed: ${checkout.stderr.trim()}`);
  }

  // Apply diff (if any).
  if (bundle.git.uncommitted_diff && bundle.git.uncommitted_diff.trim().length > 0) {
    const apply = await runCommandWithStdin(
      'git',
      ['apply', '-'],
      bundle.git.uncommitted_diff,
      { cwd: workdir },
    );
    if (apply.code !== 0) {
      throw new Error(`git apply failed: ${apply.stderr.trim()}`);
    }
  }

  // Write untracked files.
  for (const entry of bundle.git.untracked_files) {
    if (entry.skipped) continue;
    if (!entry.path || entry.content === undefined) continue;
    const full = path.join(workdir, entry.path);
    await fs.mkdir(path.dirname(full), { recursive: true });
    if (entry.encoding === 'base64') {
      await fs.writeFile(full, Buffer.from(entry.content, 'base64'));
    } else {
      await fs.writeFile(full, entry.content, 'utf-8');
    }
  }
};
