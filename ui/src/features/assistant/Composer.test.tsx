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
import { ProtocolSseTransportAdapter } from "@langchain/langgraph-sdk"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
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
    selection,
    onSelectionChange,
  }: {
    selection: ModelSelection | null
    onSelectionChange: (value: ModelSelection | null) => void
  }) => (
    <>
      <span>{selection ? "Explicit model" : "Auto active"}</span>
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
  return onNew.mock.calls.at(-1)![0].runConfig?.custom
}

async function respond(steered = false) {
  const commandId = onNew.mock.calls.length
  const runId = steered ? "existing-run" : `run-${commandId}`
  const result = { run_id: runId, ...(steered ? { steered: true } : {}) }
  const response = { id: commandId, type: "success", result }
  fetcher.mockResolvedValueOnce(Response.json(response))
  const transport = new ProtocolSseTransportAdapter({
    apiUrl: "http://localhost",
    threadId: "thread-1",
    fetch: runStarts.fetch,
  })
  expect(
    await transport.send({
      id: commandId,
      method: "run.start",
      params: {
        assistant_id: "agent",
        input: null,
        config: { configurable: onNew.mock.calls.at(-1)![0].runConfig?.custom },
      },
    })
  ).toEqual(response)
  act(() => accepted(runId))
}

beforeEach(() => {
  onNew.mockReset().mockResolvedValue(undefined)
  fetcher.mockReset()
  runStarts = createRunStartTracker(fetcher)
})
afterEach(cleanup)

describe("assistant composer model selection", () => {
  it("consumes Auto once after acceptance while retaining Auto and other settings", async () => {
    render(<Harness />)
    fireEvent.click(screen.getByText("Auto"))
    expect(await send()).toMatchObject({
      model_selection: "auto",
      model_selection_changed: true,
    })
    await respond()
    expect(screen.getByText("Auto active")).toBeTruthy()
    expect(await send()).toMatchObject({
      model_selection: "auto",
      model_selection_changed: false,
      repo: "langchain-ai/open-swe",
    })
  })

  it("retains the action when a submission fails before acceptance", async () => {
    render(<Harness />)
    fireEvent.click(screen.getByText("Auto"))
    onNew.mockRejectedValueOnce(new Error("Network unavailable"))
    await send()
    expect(await send()).toMatchObject({ model_selection_changed: true })
    await respond()
    expect(await send()).toMatchObject({ model_selection_changed: false })
  })

  it("preserves a newer picker action made before the previous run is accepted", async () => {
    render(<Harness />)
    fireEvent.click(screen.getByText("Auto"))
    await send()
    fireEvent.click(screen.getByText("Explicit"))
    fireEvent.click(screen.getByText("Auto"))
    await respond()
    expect(await send()).toMatchObject({ model_selection_changed: true })
    await respond()
    expect(await send()).toMatchObject({ model_selection_changed: false })
  })

  it("consumes accepted Auto after unrelated composer settings change", async () => {
    render(<Harness />)
    fireEvent.click(screen.getByText("Auto"))
    await send()
    act(() => {
      const current = runtime.thread.composer.getState().runConfig
      runtime.thread.composer.setRunConfig({
        ...current,
        custom: {
          ...current.custom,
          repo: "langchain-ai/langgraph",
          environment: "oss",
        },
      })
    })
    await respond()
    expect(await send()).toMatchObject({
      model_selection: "auto",
      model_selection_changed: false,
      repo: "langchain-ai/langgraph",
      environment: "oss",
    })
  })

  it("clears a pending Auto action when an explicit model is selected", async () => {
    render(<Harness />)
    fireEvent.click(screen.getByText("Auto"))
    fireEvent.click(screen.getByText("Explicit"))
    expect(await send()).toMatchObject({
      model_selection: "explicit",
      model_selection_changed: false,
    })
  })

  it("preserves Auto after steering until a new run accepts the model settings", async () => {
    render(<Harness />)
    fireEvent.click(screen.getByText("Auto"))
    expect(await send()).toMatchObject({ model_selection_changed: true })
    await respond(true)
    expect(screen.getByText("Auto active")).toBeTruthy()
    expect(await send()).toMatchObject({
      model_selection: "auto",
      model_selection_changed: true,
    })
    await respond()
    expect(await send()).toMatchObject({
      model_selection: "auto",
      model_selection_changed: false,
    })
  })
})
