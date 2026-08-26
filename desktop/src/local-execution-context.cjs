const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

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
        ? { fileName: image.fileName.slice(0, 512) }
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

function cleanPending(value) {
  if (!isRecord(value)) return null;
  return {
    prompt: stringOrNull(value.prompt, 2_000_000) || "",
    images: cleanImages(value.images),
    skills: cleanSkills(value.skills),
  };
}

function cleanCheckpoint(value) {
  if (!isRecord(value)) return { repo: null, ref: null, branch: null };
  const repo = stringOrNull(value.repo, 8_192);
  return {
    repo: repo && path.isAbsolute(repo) ? path.normalize(repo) : null,
    ref: stringOrNull(value.ref, 1_024),
    branch: stringOrNull(value.branch, 1_024),
  };
}

function normalizeContext(value) {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    !value.id ||
    value.id.length > 128 ||
    typeof value.cwd !== "string" ||
    !path.isAbsolute(value.cwd)
  ) {
    return null;
  }
  return {
    id: value.id,
    cwd: path.normalize(value.cwd),
    modelId: stringOrNull(value.modelId),
    effort: stringOrNull(value.effort),
    checkpoint: cleanCheckpoint(value.checkpoint),
    managedWorktree: value.managedWorktree === true,
    pending: cleanPending(value.pending),
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

class LocalExecutionContext {
  constructor(filePath, options = {}) {
    if (typeof filePath !== "string" || !path.isAbsolute(filePath))
      throw new Error("Local execution context path must be absolute");
    this.filePath = filePath;
    this.fs = options.fs || fs;
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
      const context = normalizeContext(value);
      if (context) this.threads.set(context.id, context);
    }
  }

  persist() {
    atomicWrite(this.filePath, [...this.threads.values()], this.fs);
  }

  get(id) {
    const value = this.threads.get(id);
    return value ? structuredClone(value) : null;
  }

  create(input) {
    const context = normalizeContext({
      id: input.id,
      cwd: input.cwd,
      modelId: input.modelId || null,
      effort: input.effort || null,
      checkpoint: { repo: null, ref: null, branch: null },
      managedWorktree: input.managedWorktree === true,
      pending:
        input.pending === null
          ? null
          : {
              prompt: String(input.prompt || ""),
              images: Array.isArray(input.images) ? input.images : [],
              skills: Array.isArray(input.skills) ? input.skills : [],
            },
    });
    if (!context) throw new Error("Invalid local execution context");
    this.threads.set(context.id, context);
    this.persist();
    return structuredClone(context);
  }

  update(id, patch) {
    const current = this.threads.get(id);
    if (!current) return null;
    const next = normalizeContext({ ...current, ...patch });
    if (!next) throw new Error("Invalid local execution context update");
    this.threads.set(id, next);
    this.persist();
    return structuredClone(next);
  }

  setCheckpoint(id, checkpoint) {
    return this.update(id, { checkpoint });
  }

  pendingPrompt(id) {
    return this.get(id)?.pending || null;
  }

  clearPrompt(id) {
    return this.update(id, { pending: null });
  }

  delete(id) {
    const deleted = this.threads.delete(id);
    if (deleted) this.persist();
    return deleted;
  }
}

module.exports = { LocalExecutionContext };
