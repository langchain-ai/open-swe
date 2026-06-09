import type { ImageChunk } from "./types";

const STORAGE_KEY = (threadId: string) => `open-swe:pending-prompts:${threadId}`;

export interface PendingPrompt {
  prompt: string;
  insertAt: number;
  images?: Array<ImageChunk>;
  modelId?: string | null;
  effort?: string | null;
}

function isImageChunk(value: unknown): value is ImageChunk {
  const image = value as { base64?: unknown; kind?: unknown; mimeType?: unknown };
  return (
    typeof value === "object" &&
    value !== null &&
    image.kind === "image" &&
    typeof image.base64 === "string" &&
    typeof image.mimeType === "string"
  );
}

function isPendingPrompt(value: unknown): value is PendingPrompt {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as PendingPrompt).prompt === "string" &&
    typeof (value as PendingPrompt).insertAt === "number" &&
    ((value as PendingPrompt).images === undefined ||
      (Array.isArray((value as PendingPrompt).images) &&
        (value as PendingPrompt).images!.every(isImageChunk)))
  );
}

function safeRead(threadId: string): Array<PendingPrompt> {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY(threadId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isPendingPrompt) : [];
  } catch {
    return [];
  }
}

function safeWrite(threadId: string, prompts: Array<PendingPrompt>): void {
  if (typeof window === "undefined") return;
  if (prompts.length === 0) {
    window.sessionStorage.removeItem(STORAGE_KEY(threadId));
    return;
  }
  try {
    window.sessionStorage.setItem(STORAGE_KEY(threadId), JSON.stringify(prompts));
  } catch {
    window.sessionStorage.removeItem(STORAGE_KEY(threadId));
  }
}

export function getPendingPrompts(threadId: string): Array<PendingPrompt> {
  return safeRead(threadId);
}

export function addPendingPrompt(
  threadId: string,
  prompt: string,
  insertAt: number,
  options?: {
    images?: Array<ImageChunk>;
    modelId?: string | null;
    effort?: string | null;
  },
): void {
  safeWrite(threadId, [
    ...safeRead(threadId),
    {
      prompt,
      insertAt,
      images: options?.images,
      modelId: options?.modelId ?? null,
      effort: options?.effort ?? null,
    },
  ]);
}

export function dropPendingPrompts(
  threadId: string,
  predicate: (entry: PendingPrompt) => boolean,
): Array<PendingPrompt> {
  const next = safeRead(threadId).filter((p) => !predicate(p));
  safeWrite(threadId, next);
  return next;
}
