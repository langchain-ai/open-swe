const STORAGE_KEY = (threadId: string) => `open-swe:pending-prompts:${threadId}`;

function safeRead(threadId: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY(threadId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((s): s is string => typeof s === "string") : [];
  } catch {
    return [];
  }
}

function safeWrite(threadId: string, prompts: string[]): void {
  if (typeof window === "undefined") return;
  if (prompts.length === 0) {
    window.sessionStorage.removeItem(STORAGE_KEY(threadId));
    return;
  }
  window.sessionStorage.setItem(STORAGE_KEY(threadId), JSON.stringify(prompts));
}

export function getPendingPrompts(threadId: string): string[] {
  return safeRead(threadId);
}

export function addPendingPrompt(threadId: string, prompt: string): void {
  safeWrite(threadId, [...safeRead(threadId), prompt]);
}

export function dropPendingPrompts(threadId: string, predicate: (prompt: string) => boolean): string[] {
  const next = safeRead(threadId).filter((p) => !predicate(p));
  safeWrite(threadId, next);
  return next;
}
