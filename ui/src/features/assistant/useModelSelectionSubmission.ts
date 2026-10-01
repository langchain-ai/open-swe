import { useCallback } from "react"
import type { AssistantRuntime } from "@assistant-ui/react"
import type { createRunStartTracker } from "./runStartTracker"

/** Consume the picker's action only after a new run accepts its settings. */
export function useModelSelectionSubmission(
  runtime: AssistantRuntime,
  runStarts: ReturnType<typeof createRunStartTracker>
) {
  const composer = runtime.thread.composer

  return useCallback(
    (runId: string) => {
      const actionId = runStarts.consumeModelSelectionAction(runId)
      const current = composer.getState().runConfig
      if (
        typeof actionId !== "string" ||
        current.custom?.model_selection_action_id !== actionId ||
        current.custom.model_selection_changed !== true
      )
        return
      composer.setRunConfig({
        ...current,
        custom: { ...current.custom, model_selection_changed: false },
      })
    },
    [composer, runStarts]
  )
}
