/** Preserve command results that the SDK's onCreated callback omits. */
export function createRunStartTracker(fetcher: typeof fetch) {
  const started = new Set<string>()
  const trackedFetch = Object.assign(
    async (...args: Parameters<typeof fetch>) => {
      const [input, init] = args
      const response = await fetcher(...args)
      if (
        !response.ok ||
        response.status === 202 ||
        response.status === 204 ||
        init?.method !== "POST" ||
        typeof init.body !== "string"
      )
        return response
      const url = new URL(
        input instanceof Request ? input.url : input.toString()
      )
      if (!url.pathname.endsWith("/commands")) return response
      const command: unknown = JSON.parse(init.body)
      if (!isRecord(command) || command.method !== "run.start") return response
      const body: unknown = await response.clone().json()
      if (
        isRecord(body) &&
        body.type === "success" &&
        isRecord(body.result) &&
        typeof body.result.run_id === "string" &&
        body.result.steered !== true
      ) {
        started.add(body.result.run_id)
      }
      return response
    },
    fetcher
  )
  return {
    fetch: trackedFetch,
    consumeStarted: (runId: string) => started.delete(runId),
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
