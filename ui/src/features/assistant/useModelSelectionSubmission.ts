import { useCallback, useEffect, useRef } from "react"
import type { AppendMessage, AssistantRuntime } from "@assistant-ui/react"

/** Consume the composer's picker action only once its run is accepted. */
export function useModelSelectionSubmission(runtime: AssistantRuntime) {
  const submitted = useRef<AppendMessage["runConfig"]>(undefined)
  const composer = runtime.thread.composer

  useEffect(
    () =>
      composer.unstable_on("send", () => {
        submitted.current = composer.getState().runConfig
      }),
    [composer]
  )

  return useCallback(() => {
    const sent = submitted.current
    submitted.current = undefined
    const current = composer.getState().runConfig
    if (current !== sent || current.custom?.model_selection_changed !== true)
      return
    composer.setRunConfig({
      ...current,
      custom: { ...current.custom, model_selection_changed: false },
    })
  }, [composer])
}
