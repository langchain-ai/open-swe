import { useCallback, useEffect, useRef } from "react"
import type { AppendMessage, AssistantRuntime } from "@assistant-ui/react"
import type { createRunStartTracker } from "./runStartTracker"

/** Consume the picker's action only after a new run accepts its settings. */
export function useModelSelectionSubmission(
  runtime: AssistantRuntime,
  runStarts: ReturnType<typeof createRunStartTracker>
) {
  const submitted = useRef<AppendMessage["runConfig"]>(undefined)
  const composer = runtime.thread.composer

  useEffect(
    () =>
      composer.unstable_on("send", () => {
        submitted.current = composer.getState().runConfig
      }),
    [composer]
  )

  return useCallback(
    (runId: string) => {
      if (!runStarts.consumeStarted(runId)) return
      const sent = submitted.current
      submitted.current = undefined
      const current = composer.getState().runConfig
      if (current !== sent || current.custom?.model_selection_changed !== true)
        return
      composer.setRunConfig({
        ...current,
        custom: { ...current.custom, model_selection_changed: false },
      })
    },
    [composer, runStarts]
  )
}
