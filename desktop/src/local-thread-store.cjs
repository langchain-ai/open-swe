const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

/**
 * What this Mac knows about its "This Mac" threads that the backend does not:
 * which checkout each one works in, the worktrees the app made for it, the
 * diff baseline, and the bridge that lets the cloud agent reach it.
 *
 * Everything else about a thread — its title, transcript, read state — is the
 * backend's, as for any cloud thread. Ids are the cloud thread ids.
 */

const BRIDGE_ID = /^[0-9a-f]{32}$/;

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringOrNull(value, maximum = 512) {
  return typeof value === "string" && value.length <= maximum ? value : null;
}

function cleanPaths(value) {
  if (!Array.isArray(value)) return [];
  const paths = value
    .filter(
      (item) =>
        typeof item === "string" &&
        item.length <= 8_192 &&
        path.isAbsolute(item),
    )
    .map((item) => path.normalize(item));
  return [...new Set(paths)];
}

function cleanBridgeId(value) {
  return typeof value === "string" && BRIDGE_ID.test(value) ? value : null;
}

function normalizeThread(value) {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    !value.id ||
    typeof value.cwd !== "string" ||
    !path.isAbsolute(value.cwd) ||
    !Number.isFinite(value.createdAt) ||
    !Number.isFinite(value.updatedAt)
  ) {
    return null;
  }
  const checkpoint = isRecord(value.checkpoint)
    ? {
        repo: stringOrNull(value.checkpoint.repo, 8_192),
        ref: stringOrNull(value.checkpoint.ref, 1_024),
        branch: stringOrNull(value.checkpoint.branch, 1_024),
      }
    : { repo: null, ref: null, branch: null };
  const worktreePath = stringOrNull(value.worktreePath, 8_192);
  return {
    id: value.id,
    cwd: path.normalize(value.cwd),
    worktreePath:
      worktreePath && path.isAbsolute(worktreePath)
        ? path.normalize(worktreePath)
        : null,
    ownedWorktrees: cleanPaths(value.ownedWorktrees),
    bridgeId: cleanBridgeId(value.bridgeId),
    createdAt: value.createdAt,
    updatedAt: value.updatedAt,
    checkpoint,
  };
}

function atomicWrite(filePath, value, fileSystem = fs) {
  fileSystem.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  try {
    fileSystem.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, {
      mode: 0o600,
    });
    fileSystem.renameSync(temporary, filePath);
  } finally {
    try {
      fileSystem.rmSync(temporary, { force: true });
    } catch {}
  }
}

class LocalThreadStore {
  constructor(filePath, options = {}) {
    this.filePath = filePath;
    this.fs = options.fs || fs;
    this.now = options.now || Date.now;
    this.threads = new Map();
    this.load();
  }

  load() {
    let values = [];
    try {
      const parsed = JSON.parse(this.fs.readFileSync(this.filePath, "utf8"));
      values = Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      if (error?.code !== "ENOENT")
        console.warn("Could not read the local thread store", error);
    }
    for (const value of values) {
      const thread = normalizeThread(value);
      if (thread) this.threads.set(thread.id, thread);
    }
  }

  persist() {
    atomicWrite(this.filePath, [...this.threads.values()], this.fs);
  }

  list() {
    return [...this.threads.values()]
      .sort(
        (left, right) =>
          right.createdAt - left.createdAt || left.id.localeCompare(right.id),
      )
      .map((thread) => structuredClone(thread));
  }

  get(id) {
    const thread = this.threads.get(id);
    return thread ? structuredClone(thread) : null;
  }

  /** Record a thread the renderer is about to create in the cloud under `id`. */
  create({ id, cwd }) {
    if (typeof id !== "string" || !id || this.threads.has(id))
      throw new Error("Invalid local thread id");
    const now = this.now();
    this.threads.set(id, {
      id,
      cwd,
      worktreePath: null,
      ownedWorktrees: [],
      bridgeId: null,
      createdAt: now,
      updatedAt: now,
      checkpoint: { repo: null, ref: null, branch: null },
    });
    this.persist();
    return this.get(id);
  }

  setBridge(id, bridgeId) {
    const current = this.threads.get(id);
    if (!current) return null;
    const next = cleanBridgeId(bridgeId);
    if (!next) throw new Error("Invalid bridge id");
    this.threads.set(id, { ...current, bridgeId: next, updatedAt: this.now() });
    this.persist();
    return this.get(id);
  }

  /**
   * `null` moves the thread back into the project's own checkout. `owned` marks
   * a worktree this app created: it stays recorded even after the thread moves
   * off it, so nothing the app made is left behind when the thread is deleted.
   */
  setWorktree(id, worktreePath, owned = false) {
    const current = this.threads.get(id);
    if (!current) return null;
    if (
      worktreePath !== null &&
      (typeof worktreePath !== "string" || !path.isAbsolute(worktreePath))
    )
      throw new Error("Invalid worktree path");
    const next = worktreePath && path.normalize(worktreePath);
    this.threads.set(id, {
      ...current,
      worktreePath: next,
      ownedWorktrees:
        owned && next
          ? [...new Set([...current.ownedWorktrees, next])]
          : current.ownedWorktrees,
      updatedAt: this.now(),
    });
    this.persist();
    return this.get(id);
  }

  setCheckpoint(id, checkpoint) {
    const current = this.threads.get(id);
    if (!current) return null;
    const next = {
      ...current,
      checkpoint: {
        repo: checkpoint.repo,
        ref: checkpoint.ref,
        branch: stringOrNull(checkpoint.branch, 1_024),
      },
      updatedAt: this.now(),
    };
    this.threads.set(id, next);
    this.persist();
    return this.get(id);
  }

  delete(id) {
    const current = this.threads.get(id);
    if (!current) return null;
    this.threads.delete(id);
    this.persist();
    return structuredClone(current);
  }
}

module.exports = { LocalThreadStore, atomicWrite };
