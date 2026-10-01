/** Match command acceptance to its submitted human message, not stream completion. */
export function createRunAcceptanceTracker(fetcher: typeof fetch) {
  const submissions = new Map<string, { response?: Promise<boolean> }>()
  const trackedFetch = Object.assign((...args: Parameters<typeof fetch>) => {
    const [input, init] = args
    const response = fetcher(...args)
    if (init?.method !== "POST" || typeof init.body !== "string")
      return response
    const url = new URL(input instanceof Request ? input.url : input.toString())
    const isCommand = url.pathname.endsWith("/commands")
    if (!isCommand && !url.pathname.endsWith("/runs")) return response
    const command: unknown = JSON.parse(init.body)
    if (!isRecord(command) || (isCommand && command.method !== "run.start"))
      return response
    const params = isCommand ? command.params : command
    if (!isRecord(params) || !isRecord(params.input)) return response
    const messages = params.input.messages
    if (!Array.isArray(messages)) return response
    for (const message of messages) {
      if (!isRecord(message) || typeof message.id !== "string") continue
      const submission = submissions.get(message.id)
      if (!submission) continue
      submission.response = response
        .then(async (result) => {
          if (!result.ok || result.status === 202 || result.status === 204)
            return false
          const body: unknown = await result.clone().json()
          const run =
            isCommand && isRecord(body) && body.type === "success"
              ? body.result
              : isCommand
                ? undefined
                : body
          return isRecord(run) && typeof run.run_id === "string"
        })
        .catch(() => false)
    }
    return response
  }, fetcher)
  return {
    fetch: trackedFetch,
    track(messageId: string) {
      const submission: { response?: Promise<boolean> } = {}
      submissions.set(messageId, submission)
      return async () => {
        try {
          return (await submission.response) ?? false
        } finally {
          if (submissions.get(messageId) === submission)
            submissions.delete(messageId)
        }
      }
    },
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
