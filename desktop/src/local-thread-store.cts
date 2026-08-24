import { randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const MUTABLE_FIELDS = new Set(["title", "modelId", "effort", "viewed"]);
const SKILL_NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

interface LocalImage {
  kind: string;
  base64: string;
  mimeType: string;
  fileName?: string;
}

interface LocalSkill {
  name: string;
  description: string;
  instructions: string;
}

interface PendingPrompt {
  prompt: string;
  images: LocalImage[];
  skills: LocalSkill[];
}

interface LocalCheckpoint {
  repo: string | null;
  ref: string | null;
  branch: string | null;
}

export interface LocalThread {
  id: string;
  cwd: string;
  title: string;
  modelId: string | null;
  effort: string | null;
  viewed: boolean;
  createdAt: number;
  updatedAt: number;
  checkpoint: LocalCheckpoint;
  pending: PendingPrompt | null;
}

interface CreateThreadInput {
  cwd: string;
  prompt?: unknown;
  images?: unknown;
  skills?: unknown;
  modelId?: unknown;
  effort?: unknown;
}

interface ThreadStoreOptions {
  fs?: typeof fs;
  now?: () => number;
  uuid?: () => string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringOrNull(value: unknown, maximum = 512): string | null {
  return typeof value === "string" && value.length <= maximum ? value : null;
}

function cleanImages(value: unknown): LocalImage[] {
  if (!Array.isArray(value)) return [];
  const images: LocalImage[] = [];
  for (const candidate of value) {
    if (
      !isRecord(candidate) ||
      typeof candidate.base64 !== "string" ||
      candidate.base64.length > 20_000_000 ||
      typeof candidate.mimeType !== "string" ||
      candidate.mimeType.length > 200
    ) {
      continue;
    }
    images.push({
      kind: typeof candidate.kind === "string" ? candidate.kind : "image",
      base64: candidate.base64,
      mimeType: candidate.mimeType,
      ...(typeof candidate.fileName === "string"
        ? { fileName: candidate.fileName }
        : {}),
    });
  }
  return images;
}

function cleanSkills(value: unknown): LocalSkill[] {
  if (!Array.isArray(value)) return [];
  const skills: LocalSkill[] = [];
  for (const candidate of value) {
    if (
      !isRecord(candidate) ||
      typeof candidate.name !== "string" ||
      candidate.name.length > 64 ||
      !SKILL_NAME.test(candidate.name) ||
      typeof candidate.description !== "string" ||
      !candidate.description.trim() ||
      candidate.description.length > 1_024 ||
      typeof candidate.instructions !== "string" ||
      candidate.instructions.length > 20_000
    ) {
      continue;
    }
    skills.push({
      name: candidate.name,
      description: candidate.description.trim(),
      instructions: candidate.instructions,
    });
  }
  return skills;
}

function normalizeThread(value: unknown): LocalThread | null {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    !value.id ||
    typeof value.cwd !== "string" ||
    !path.isAbsolute(value.cwd) ||
    typeof value.title !== "string" ||
    typeof value.createdAt !== "number" ||
    !Number.isFinite(value.createdAt) ||
    typeof value.updatedAt !== "number" ||
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
  return {
    id: value.id,
    cwd: path.normalize(value.cwd),
    title: value.title.slice(0, 80) || "New local agent",
    modelId: stringOrNull(value.modelId),
    effort: stringOrNull(value.effort),
    viewed: value.viewed !== false,
    createdAt: value.createdAt,
    updatedAt: value.updatedAt,
    checkpoint,
    pending,
  };
}

export function atomicWrite(
  filePath: string,
  value: unknown,
  fileSystem: typeof fs = fs,
): void {
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

export function sessionTitle(text: string): string {
  const value = text.trim().replace(/\s+/g, " ");
  return value.slice(0, 80) || "New local agent";
}

export class LocalThreadStore {
  readonly filePath: string;
  readonly fs: typeof fs;
  readonly now: () => number;
  readonly uuid: () => string;
  private readonly threads = new Map<string, LocalThread>();

  constructor(filePath: string, options: ThreadStoreOptions = {}) {
    this.filePath = filePath;
    this.fs = options.fs || fs;
    this.now = options.now || Date.now;
    this.uuid = options.uuid || randomUUID;
    this.load();
  }

  private load(): void {
    let values: unknown[] = [];
    try {
      const parsed: unknown = JSON.parse(
        this.fs.readFileSync(this.filePath, "utf8"),
      );
      values = Array.isArray(parsed) ? parsed : [];
    } catch {}
    for (const value of values) {
      const thread = normalizeThread(value);
      if (thread) this.threads.set(thread.id, thread);
    }
  }

  private persist(): void {
    atomicWrite(this.filePath, [...this.threads.values()], this.fs);
  }

  list(): LocalThread[] {
    return [...this.threads.values()]
      .sort(
        (left, right) =>
          right.createdAt - left.createdAt || left.id.localeCompare(right.id),
      )
      .map((thread) => structuredClone(thread));
  }

  get(id: string): LocalThread | null {
    const thread = this.threads.get(id);
    return thread ? structuredClone(thread) : null;
  }

  create(input: CreateThreadInput): LocalThread {
    const now = this.now();
    const prompt = typeof input.prompt === "string" ? input.prompt : "";
    const thread: LocalThread = {
      id: this.uuid(),
      cwd: input.cwd,
      title: sessionTitle(prompt),
      modelId: stringOrNull(input.modelId),
      effort: stringOrNull(input.effort),
      viewed: true,
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
    return structuredClone(thread);
  }

  update(id: string, patch: unknown): LocalThread | null {
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
    if (Object.keys(patch).some((key) => key !== "viewed"))
      next.updatedAt = this.now();
    this.threads.set(id, next);
    this.persist();
    return structuredClone(next);
  }

  setCheckpoint(id: string, checkpoint: LocalCheckpoint): LocalThread | null {
    const current = this.threads.get(id);
    if (!current) return null;
    const next: LocalThread = {
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
    return structuredClone(next);
  }

  pendingPrompt(id: string): PendingPrompt | null {
    const pending = this.threads.get(id)?.pending;
    return pending ? structuredClone(pending) : null;
  }

  clearPrompt(id: string): LocalThread | null {
    const current = this.threads.get(id);
    if (!current?.pending) return null;
    const next = { ...current, pending: null, updatedAt: this.now() };
    this.threads.set(id, next);
    this.persist();
    return structuredClone(next);
  }

  delete(id: string): LocalThread | null {
    const current = this.threads.get(id);
    if (!current) return null;
    this.threads.delete(id);
    this.persist();
    return structuredClone(current);
  }
}
