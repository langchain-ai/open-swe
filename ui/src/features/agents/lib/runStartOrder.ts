const tails = new Map<string, Promise<unknown>>()

/**
 * Send a thread's run starts one at a time, each once the server has answered
 * the one before, so it handles them in the order they were sent.
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

/** A `fetch` that sends each thread's run starts, on the runs routes, in order. */
export function orderRunStarts(fetcher: typeof fetch): typeof fetch {
  return Object.assign((...args: Parameters<typeof fetch>) => {
    const start = runStartOf(...args)
    return start
      ? inSendOrder(start.threadId, () => fetcher(...args))
      : fetcher(...args)
  }, fetcher)
}

/**
 * The run start a request is: `POST .../threads/{id}/runs/stream`, which
 * streams the run, or `POST .../threads/{id}/runs`, which creates it.
 */
export function runStartOf(
  ...[input, init]: Parameters<typeof fetch>
): { threadId: string; streamed: boolean } | null {
  if (init?.method !== "POST") return null
  const url = new URL(input instanceof Request ? input.url : input.toString())
  const match = /\/threads\/([^/]+)\/runs(\/stream)?$/.exec(url.pathname)
  return match?.[1]
    ? { threadId: decodeURIComponent(match[1]), streamed: Boolean(match[2]) }
    : null
}
