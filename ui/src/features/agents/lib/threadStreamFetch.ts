/** A thread subscription is persistent. EOF is a disconnect, even with HTTP 200. */
export function threadStreamFetch(
  fetchImpl: typeof fetch,
  activity: (connected: boolean) => void
): typeof fetch {
  return async (input, init) => {
    if (String(input).endsWith("/commands")) {
      init = {
        ...init,
        signal: AbortSignal.any([
          ...(init?.signal ? [init.signal] : []),
          AbortSignal.timeout(30_000),
        ]),
      }
    }
    let response: Response
    try {
      response = await fetchImpl(input, init)
    } catch (error) {
      if (!init?.signal?.aborted && String(input).includes("/stream/events"))
        activity(false)
      throw error
    }
    if (
      !response.ok ||
      !response.body ||
      !response.headers.get("content-type")?.includes("text/event-stream")
    )
      return response
    const reader = response.body.getReader()
    const body = new ReadableStream<Uint8Array>({
      async pull(controller) {
        try {
          const { done, value } = await reader.read()
          if (done) {
            if (init?.signal?.aborted) controller.close()
            else {
              activity(false)
              controller.error(
                new Error("Thread connection closed; reconnecting")
              )
            }
          } else {
            activity(true)
            controller.enqueue(value)
          }
        } catch (error) {
          if (!init?.signal?.aborted) activity(false)
          controller.error(error)
        }
      },
      cancel: (reason) => reader.cancel(reason),
    })
    return new Response(body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    })
  }
}
