const path = require("node:path");

function title(prompt) {
  return String(prompt || "").trim().replace(/\s+/g, " ").slice(0, 80) || "New local agent";
}

class LocalExecutionContext {
  constructor() {
    this.threads = new Map();
  }

  list() {
    return [...this.threads.values()].map(structuredClone);
  }

  get(id) {
    const value = this.threads.get(id);
    return value ? structuredClone(value) : null;
  }

  create(input) {
    if (typeof input.id !== "string" || !input.id) throw new Error("Thread id is required");
    const now = Date.now();
    const thread = {
      id: input.id,
      cwd: path.normalize(input.cwd),
      title: title(input.prompt),
      modelId: input.modelId || null,
      effort: input.effort || null,
      viewed: true,
      createdAt: now,
      updatedAt: now,
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
    };
    this.threads.set(thread.id, thread);
    return structuredClone(thread);
  }

  update(id, patch) {
    const current = this.threads.get(id);
    if (!current) return null;
    const next = { ...current, ...patch, updatedAt: Date.now() };
    this.threads.set(id, next);
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
    return this.threads.delete(id);
  }
}

module.exports = { LocalExecutionContext };
