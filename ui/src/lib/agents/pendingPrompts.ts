const STORAGE_KEY = (threadId: string) => `open-swe:pending-prompts:${threadId}`;

export interface PendingPrompt {
  prompt: string;
  insertAt: number;
}

function isPendingPrompt(value: unknown): value is PendingPrompt {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as PendingPrompt).prompt === "string" &&
    typeof (value as PendingPrompt).insertAt === "number"
  );
}

function safeRead(threadId: string): PendingPrompt[] {
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

function safeWrite(threadId: string, prompts: PendingPrompt[]): void {
  if (typeof window === "undefined") return;
  if (prompts.length === 0) {
    window.sessionStorage.removeItem(STORAGE_KEY(threadId));
    return;
  }
  window.sessionStorage.setItem(STORAGE_KEY(threadId), JSON.stringify(prompts));
}

export function getPendingPrompts(threadId: string): PendingPrompt[] {
  return safeRead(threadId);
}

export function addPendingPrompt(threadId: string, prompt: string, insertAt: number): void {
  safeWrite(threadId, [...safeRead(threadId), { prompt, insertAt }]);
}

export function dropPendingPrompts(
  threadId: string,
  predicate: (entry: PendingPrompt) => boolean,
): PendingPrompt[] {
  const next = safeRead(threadId).filter((p) => !predicate(p));
  safeWrite(threadId, next);
  return next;
}
