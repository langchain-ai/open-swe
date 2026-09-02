const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

const MUTABLE_FIELDS = new Set([
  "title",
  "modelId",
  "effort",
  "viewed",
  "archived",
]);
const SKILL_NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringOrNull(value, maximum = 512) {
  return typeof value === "string" && value.length <= maximum ? value : null;
}

function cleanImages(value) {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (image) =>
        isRecord(image) &&
        typeof image.base64 === "string" &&
        image.base64.length <= 20_000_000 &&
        typeof image.mimeType === "string" &&
        image.mimeType.length <= 200,
    )
    .map((image) => ({
      kind: typeof image.kind === "string" ? image.kind : "image",
      base64: image.base64,
      mimeType: image.mimeType,
      ...(typeof image.fileName === "string"
        ? { fileName: image.fileName }
        : {}),
    }));
}

function cleanSkills(value) {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (skill) =>
        isRecord(skill) &&
        typeof skill.name === "string" &&
        skill.name.length <= 64 &&
        SKILL_NAME.test(skill.name) &&
        typeof skill.description === "string" &&
        skill.description.trim() &&
        skill.description.length <= 1_024 &&
        typeof skill.instructions === "string" &&
        skill.instructions.length <= 20_000,
    )
    .map(({ name, description, instructions }) => ({
      name,
      description: description.trim(),
      instructions,
    }));
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

function normalizeThread(value) {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    !value.id ||
    typeof value.cwd !== "string" ||
    !path.isAbsolute(value.cwd) ||
    typeof value.title !== "string" ||
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
  const pending = isRecord(value.pending)
    ? {
        prompt: stringOrNull(value.pending.prompt, 2_000_000) || "",
        images: cleanImages(value.pending.images),
        skills: cleanSkills(value.pending.skills),
      }
    : null;
  const worktreePath = stringOrNull(value.worktreePath, 8_192);
  return {
    id: value.id,
    cwd: path.normalize(value.cwd),
    worktreePath:
      worktreePath && path.isAbsolute(worktreePath)
        ? path.normalize(worktreePath)
        : null,
    ownedWorktrees: cleanPaths(value.ownedWorktrees),
    title: value.title.slice(0, 80) || "New local agent",
    modelId: stringOrNull(value.modelId),
    effort: stringOrNull(value.effort),
    viewed: value.viewed !== false,
    archived: value.archived === true,
    createdAt: value.createdAt,
    updatedAt: value.updatedAt,
    checkpoint,
    pending,
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

function sessionTitle(text) {
  const value = text.trim().replace(/\s+/g, " ");
  return value.slice(0, 80) || "New local agent";
}

class LocalThreadStore {
  constructor(filePath, options = {}) {
    this.filePath = filePath;
    this.fs = options.fs || fs;
    this.now = options.now || Date.now;
    this.uuid = options.uuid || randomUUID;
    this.threads = new Map();
    this.load();
  }

  load() {
    let values = [];
    try {
      const parsed = JSON.parse(this.fs.readFileSync(this.filePath, "utf8"));
      values = Array.isArray(parsed) ? parsed : [];
    } catch {}
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

  create(input) {
    const now = this.now();
    const prompt = typeof input.prompt === "string" ? input.prompt : "";
    const thread = {
      id: this.uuid(),
      cwd: input.cwd,
      worktreePath: null,
      ownedWorktrees: [],
      title: sessionTitle(prompt),
      modelId: stringOrNull(input.modelId),
      effort: stringOrNull(input.effort),
      viewed: true,
      archived: false,
      createdAt: now,
      updatedAt: now,
      checkpoint: { repo: null, ref: null, branch: null },
      pending: {
        prompt,
        images: cleanImages(input.images),
        skills: cleanSkills(input.skills),
      },
    };
    this.threads.set(thread.id, thread);
    this.persist();
    return this.get(thread.id);
  }

  update(id, patch) {
    const current = this.threads.get(id);
    if (!current) return null;
    if (!isRecord(patch)) throw new Error("Invalid local thread update");
    for (const key of Object.keys(patch)) {
      if (!MUTABLE_FIELDS.has(key))
        throw new Error(`Cannot update local thread field: ${key}`);
    }
    const next = { ...current };
    if ("title" in patch) {
      if (typeof patch.title !== "string" || !patch.title.trim())
        throw new Error("Invalid title");
      next.title = patch.title.trim().slice(0, 80);
    }
    if ("modelId" in patch) next.modelId = stringOrNull(patch.modelId);
    if ("effort" in patch) next.effort = stringOrNull(patch.effort);
    if ("viewed" in patch) {
      if (typeof patch.viewed !== "boolean")
        throw new Error("Invalid viewed state");
      next.viewed = patch.viewed;
    }
    if ("archived" in patch) {
      if (typeof patch.archived !== "boolean")
        throw new Error("Invalid archived state");
      next.archived = patch.archived;
    }
    // Neither reading nor archiving is an edit, so neither reorders the list.
    if (
      Object.keys(patch).some((key) => key !== "viewed" && key !== "archived")
    )
      next.updatedAt = this.now();
    this.threads.set(id, next);
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

  pendingPrompt(id) {
    const pending = this.threads.get(id)?.pending;
    return pending ? structuredClone(pending) : null;
  }

  clearPrompt(id) {
    const current = this.threads.get(id);
    if (!current?.pending) return null;
    this.threads.set(id, { ...current, pending: null, updatedAt: this.now() });
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

module.exports = { LocalThreadStore, atomicWrite, sessionTitle };
