const tails = new Map<string, Promise<unknown>>()

/**
 * Send a thread's `run.start`s one at a time, each once the server has
 * answered the one before, so it handles them in the order they were sent.
 * See docs/tla/SteerRace.tla.
 */
export function inSendOrder<T>(
  threadId: string,
  send: () => Promise<T>
): Promise<T> {
  const sent = (tails.get(threadId) ?? Promise.resolve()).then(send)
  const tail = sent.catch(() => undefined)
  tails.set(threadId, tail)
  void tail.then(() => {
    if (tails.get(threadId) === tail) tails.delete(threadId)
  })
  return sent
}

/** A `fetch` that sends each thread's `run.start` commands in order. */
export function orderRunStarts(fetcher: typeof fetch): typeof fetch {
  return Object.assign((...args: Parameters<typeof fetch>) => {
    const threadId = runStartThreadId(...args)
    return threadId
      ? inSendOrder(threadId, () => fetcher(...args))
      : fetcher(...args)
  }, fetcher)
}

function runStartThreadId(
  ...[input, init]: Parameters<typeof fetch>
): string | null {
  if (init?.method !== "POST" || typeof init.body !== "string") return null
  const url = new URL(input instanceof Request ? input.url : input.toString())
  const encoded = /\/threads\/([^/]+)\/commands$/.exec(url.pathname)?.[1]
  if (!encoded) return null
  const command: unknown = JSON.parse(init.body)
  return isRecord(command) && command.method === "run.start"
    ? decodeURIComponent(encoded)
    : null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
