/** @vitest-environment jsdom */

import { useExternalStoreRuntime } from "@assistant-ui/react"
import type { ThreadMessage } from "@assistant-ui/react"
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { useModelSelectionSubmission } from "./useModelSelectionSubmission"
import { createRunStartTracker } from "./runStartTracker"

afterEach(cleanup)

it.each(["failed", "steered", "newer-selection", "accepted"])(
  "consumes only Auto intent accepted by a new run (%s)",
  async (outcome) => {
    const fetcher = vi.fn<typeof fetch>()
    const runStarts = createRunStartTracker(fetcher)
    const { result } = renderHook(() => {
      const runtime = useExternalStoreRuntime<ThreadMessage>({
        messages: [],
        onNew: vi.fn(),
      })
      const accept = useModelSelectionSubmission(runtime, runStarts)
      return { composer: runtime.thread.composer, accept }
    })
    const custom = {
      model_selection: "auto",
      model_selection_changed: true,
      model_selection_action_id: "original",
    }
    act(() => result.current.composer.setRunConfig({ custom }))
    fetcher.mockResolvedValueOnce(
      Response.json({
        type: outcome === "failed" ? "error" : "success",
        result: { run_id: "run", steered: outcome === "steered" },
      })
    )
    await runStarts.fetch("http://localhost/threads/one/commands", {
      method: "POST",
      body: JSON.stringify({
        method: "run.start",
        params: { config: { configurable: custom } },
      }),
    })
    act(() => {
      if (outcome === "newer-selection")
        result.current.composer.setRunConfig({
          custom: { ...custom, model_selection_action_id: "newer" },
        })
      result.current.accept("run")
    })
    expect(
      result.current.composer.getState().runConfig.custom
        ?.model_selection_changed
    ).toBe(outcome !== "accepted")
  }
)
