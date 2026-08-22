const listeners = new Map<string, Set<() => void>>()

export function onAgentRunCreated(
  threadId: string,
  listener: () => void
): () => void {
  const threadListeners = listeners.get(threadId) ?? new Set()
  threadListeners.add(listener)
  listeners.set(threadId, threadListeners)
  return () => {
    threadListeners.delete(listener)
    if (threadListeners.size === 0) listeners.delete(threadId)
  }
}

export function notifyAgentRunCreated(threadId: string): void {
  for (const listener of listeners.get(threadId) ?? []) listener()
  listeners.delete(threadId)
}
