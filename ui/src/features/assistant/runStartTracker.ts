/** Preserve command results that the SDK's onCreated callback omits. */
export function createRunStartTracker(fetcher: typeof fetch) {
  const acceptedActions = new Map<string, string>()
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
      if (!isRecord(command.params) || !isRecord(command.params.config))
        return response
      const configurable = command.params.config.configurable
      if (
        !isRecord(configurable) ||
        configurable.model_selection !== "auto" ||
        configurable.model_selection_changed !== true ||
        typeof configurable.model_selection_action_id !== "string"
      )
        return response
      const body: unknown = await response.clone().json()
      if (
        isRecord(body) &&
        body.type === "success" &&
        isRecord(body.result) &&
        typeof body.result.run_id === "string" &&
        body.result.steered !== true
      ) {
        acceptedActions.set(
          body.result.run_id,
          configurable.model_selection_action_id
        )
      }
      return response
    },
    fetcher
  )
  return {
    fetch: trackedFetch,
    consumeModelSelectionAction: (runId: string) => {
      const actionId = acceptedActions.get(runId)
      acceptedActions.delete(runId)
      return actionId
    },
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
