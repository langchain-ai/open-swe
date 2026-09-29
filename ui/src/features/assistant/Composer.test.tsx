/** @vitest-environment jsdom */

import { useEffect } from "react"
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
} from "@assistant-ui/react"
import type {
  AppendMessage,
  AssistantRuntime,
  ThreadMessage,
} from "@assistant-ui/react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { Composer } from "./Composer"
import { useModelSelectionSubmission } from "./useModelSelectionSubmission"
import { createRunStartTracker } from "./runStartTracker"

const model = { modelId: "openai:gpt-6-sol", effort: "high" }
let runtime: AssistantRuntime
let accepted: (runId: string) => void
let runStarts: ReturnType<typeof createRunStartTracker>
const fetcher = vi.fn<typeof fetch>()
const onNew = vi.fn<(message: AppendMessage) => Promise<void>>()

vi.mock("./AssistantProvider", () => ({
  useThreadMetadata: () => ({
    data: { model: model.modelId, effort: model.effort },
  }),
}))
vi.mock("@/features/agents/lib/provider/useModelOptions", () => ({
  useModelOptions: () => ({ models: [], defaultSelection: model }),
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useWorkspaceOptions: () => ({}),
}))
vi.mock("@/lib/profile", () => ({
  useProfile: () => ({ data: {} }),
  useRepos: () => ({}),
}))
vi.mock("@/features/agents/components/ModelPicker", () => ({
  ModelPicker: ({
    onSelectionChange,
  }: {
    onSelectionChange: (value: ModelSelection | null) => void
  }) => (
    <>
      <button type="button" onClick={() => onSelectionChange(null)}>
        Auto
      </button>
      <button type="button" onClick={() => onSelectionChange(model)}>
        Explicit
      </button>
    </>
  ),
}))

function Harness() {
  const currentRuntime = useExternalStoreRuntime<ThreadMessage>({
    messages: [],
    onNew,
  })
  const accept = useModelSelectionSubmission(currentRuntime, runStarts)
  useEffect(() => {
    runtime = currentRuntime
    accepted = accept
  }, [accept, currentRuntime])
  return (
    <AssistantRuntimeProvider runtime={currentRuntime}>
      <Composer initialRepo="langchain-ai/open-swe" />
    </AssistantRuntimeProvider>
  )
}

async function send() {
  await act(async () => {
    runtime.thread.composer.setText("Continue")
    runtime.thread.composer.send()
  })
  return onNew.mock.lastCall![0].runConfig?.custom
}

async function respond(steered = false) {
  fetcher.mockResolvedValueOnce(
    Response.json({
      type: "success",
      result: { run_id: "run", steered },
    })
  )
  await runStarts.fetch("http://localhost/threads/one/commands", {
    method: "POST",
    body: JSON.stringify({
      method: "run.start",
      params: {
        config: { configurable: onNew.mock.lastCall![0].runConfig?.custom },
      },
    }),
  })
  act(() => accepted("run"))
}

beforeEach(() => {
  onNew.mockReset().mockResolvedValue(undefined)
  fetcher.mockReset()
  runStarts = createRunStartTracker(fetcher)
})
afterEach(cleanup)

it.each(["failed", "steered"])(
  "retains Auto after %s until a new run accepts it",
  async (outcome) => {
    render(<Harness />)
    fireEvent.click(screen.getByText("Auto"))
    if (outcome === "failed")
      onNew.mockRejectedValueOnce(new Error("Network unavailable"))
    expect(await send()).toMatchObject({ model_selection_changed: true })
    if (outcome === "steered") await respond(true)
    expect(await send()).toMatchObject({ model_selection_changed: true })
    await respond()
    expect(await send()).toMatchObject({
      model_selection: "auto",
      model_selection_changed: false,
      repo: "langchain-ai/open-swe",
    })
  }
)

it("preserves a newer picker action and lets an explicit choice cancel Auto", async () => {
  render(<Harness />)
  fireEvent.click(screen.getByText("Auto"))
  await send()
  fireEvent.click(screen.getByText("Explicit"))
  fireEvent.click(screen.getByText("Auto"))
  await respond()
  expect(await send()).toMatchObject({ model_selection_changed: true })
  fireEvent.click(screen.getByText("Auto"))
  fireEvent.click(screen.getByText("Explicit"))
  expect(await send()).toMatchObject({
    model_selection: "explicit",
    model_selection_changed: false,
  })
})
