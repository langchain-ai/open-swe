const fs = require("node:fs");
const path = require("node:path");

/**
 * Per-thread git state that only exists on this machine.
 *
 * A local thread itself lives on the server like any other; what cannot live
 * there is the checkpoint ref this app writes into the user's repository and
 * the branch that ref was taken from. Those are facts about this computer, so
 * they stay here, keyed by the thread they belong to.
 */
class LocalCheckpointStore {
  constructor(filePath) {
    this.filePath = filePath;
    this.records = this.read();
  }

  read() {
    try {
      const parsed = JSON.parse(fs.readFileSync(this.filePath, "utf8"));
      if (!Array.isArray(parsed)) return new Map();
      return new Map(
        parsed
          .filter(
            (record) =>
              record &&
              typeof record.id === "string" &&
              typeof record.cwd === "string" &&
              path.isAbsolute(record.cwd),
          )
          .map((record) => [
            record.id,
            {
              id: record.id,
              cwd: path.normalize(record.cwd),
              repo: typeof record.repo === "string" ? record.repo : null,
              ref: typeof record.ref === "string" ? record.ref : null,
              branch: typeof record.branch === "string" ? record.branch : null,
            },
          ]),
      );
    } catch {
      return new Map();
    }
  }

  write() {
    try {
      fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
      fs.writeFileSync(this.filePath, JSON.stringify([...this.records.values()]));
    } catch {
      /* a lost checkpoint costs the diff panel, not the thread */
    }
  }

  get(threadId) {
    return this.records.get(threadId) ?? null;
  }

  list() {
    return [...this.records.values()];
  }

  put(threadId, cwd) {
    const existing = this.get(threadId);
    const record = {
      id: threadId,
      cwd: path.normalize(cwd),
      repo: existing?.repo ?? null,
      ref: existing?.ref ?? null,
      branch: existing?.branch ?? null,
    };
    this.records.set(threadId, record);
    this.write();
    return record;
  }

  setCheckpoint(threadId, checkpoint) {
    const existing = this.get(threadId);
    if (!existing) return null;
    const record = {
      ...existing,
      repo: checkpoint.repo ?? null,
      ref: checkpoint.ref ?? null,
      branch: checkpoint.branch ?? null,
    };
    this.records.set(threadId, record);
    this.write();
    return record;
  }

  delete(threadId) {
    if (!this.records.delete(threadId)) return false;
    this.write();
    return true;
  }
}

module.exports = { LocalCheckpointStore };
