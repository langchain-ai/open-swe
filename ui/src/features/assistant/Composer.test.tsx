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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { Composer } from "./Composer"
import { useModelSelectionSubmission } from "./useModelSelectionSubmission"

const model = { modelId: "openai:gpt-6-sol", effort: "high" }
let runtime: AssistantRuntime
let accepted: () => void
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
  const accept = useModelSelectionSubmission(currentRuntime)
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

beforeEach(() => onNew.mockReset().mockResolvedValue(undefined))
afterEach(cleanup)

describe("assistant composer model selection", () => {
  it("consumes Auto once after acceptance while retaining Auto and other settings", async () => {
    render(<Harness />)
    fireEvent.click(screen.getByText("Auto"))
    expect(await send()).toMatchObject({
      model_selection: "auto",
      model_selection_changed: true,
    })
    act(accepted)
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
    act(accepted)
    expect(await send()).toMatchObject({ model_selection_changed: false })
  })

  it("preserves a newer picker action made before the previous run is accepted", async () => {
    render(<Harness />)
    fireEvent.click(screen.getByText("Auto"))
    await send()
    fireEvent.click(screen.getByText("Explicit"))
    fireEvent.click(screen.getByText("Auto"))
    act(accepted)
    expect(await send()).toMatchObject({ model_selection_changed: true })
    act(accepted)
    expect(await send()).toMatchObject({ model_selection_changed: false })
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
})
