/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ModelPicker } from "./ModelPicker"
import type { ModelOption } from "@/lib/api"

afterEach(() => cleanup())

const MODELS: Array<ModelOption> = [
  {
    id: "openai:gpt-5.6-sol",
    label: "GPT-5.6 Sol",
    efforts: ["none", "low", "medium", "high", "xhigh"],
    default_effort: "xhigh",
    supports_images: true,
    context_window: 272_000,
  },
  {
    id: "google_genai:gemini-3.7-flash",
    label: "Gemini 3.7 Flash",
    efforts: ["minimal", "low", "medium", "high"],
    default_effort: "medium",
    supports_images: true,
  },
  {
    id: "fireworks:accounts/fireworks/models/kimi-k3",
    label: "Kimi K3",
    efforts: ["low", "high", "max"],
    default_effort: "high",
    supports_images: false,
  },
]

function openPicker(
  props: Partial<React.ComponentProps<typeof ModelPicker>> = {}
) {
  const onSelectionChange = props.onSelectionChange ?? vi.fn()
  render(
    <ModelPicker
      models={MODELS}
      selection={{ modelId: "openai:gpt-5.6-sol", effort: "high" }}
      onSelectionChange={onSelectionChange}
      {...props}
    />
  )
  fireEvent.click(screen.getByRole("button", { expanded: false }))
  return { onSelectionChange, panel: screen.getByTestId("model-picker-panel") }
}

function openModelPane() {
  fireEvent.click(screen.getByRole("option", { name: "GPT-5.6 Sol" }))
}

describe("ModelPicker", () => {
  it("labels the trigger with the selected model and effort", () => {
    render(
      <ModelPicker
        models={MODELS}
        selection={{ modelId: "openai:gpt-5.6-sol", effort: "xhigh" }}
        onSelectionChange={vi.fn()}
      />
    )

    expect(
      screen.getByRole("button", { name: /GPT-5.6 Sol Extra High/ })
    ).toBeTruthy()
  })

  it("shows the selected model's context, reasoning and model row", () => {
    const { panel } = openPicker()

    expect(panel.textContent).toContain("Context")
    expect(panel.textContent).toContain("272.0K")
    expect(
      within(screen.getByRole("listbox", { name: "Reasoning effort" }))
        .getAllByRole("option")
        .map((option) => option.textContent)
    ).toEqual(["None", "Low", "Medium", "High", "Extra High"])
    expect(screen.getByRole("option", { name: "GPT-5.6 Sol" })).toBeTruthy()
    expect(screen.queryByRole("listbox", { name: "Models" })).toBeNull()
  })

  it("omits the context section for models without a context window", () => {
    openPicker({
      selection: { modelId: "google_genai:gemini-3.7-flash", effort: "medium" },
    })

    expect(screen.getByTestId("model-picker-panel").textContent).not.toContain(
      "Context"
    )
  })

  it("selects a reasoning effort for the selected model", () => {
    const { onSelectionChange } = openPicker()

    fireEvent.click(
      within(
        screen.getByRole("listbox", { name: "Reasoning effort" })
      ).getByRole("option", { name: "Low" })
    )

    expect(onSelectionChange).toHaveBeenCalledWith({
      modelId: "openai:gpt-5.6-sol",
      effort: "low",
    })
    expect(screen.queryByTestId("model-picker-panel")).toBeNull()
  })

  it("lists every model with its effort and filters on search", () => {
    openPicker()
    openModelPane()

    const models = screen.getByRole("listbox", { name: "Models" })
    expect(
      within(models)
        .getAllByRole("option")
        .map((option) => option.textContent)
    ).toEqual(["GPT-5.6 Sol High", "Gemini 3.7 Flash Medium", "Kimi K3 High"])

    fireEvent.change(screen.getByLabelText("Search models"), {
      target: { value: "kimi" },
    })

    expect(
      within(screen.getByRole("listbox", { name: "Models" }))
        .getAllByRole("option")
        .map((option) => option.textContent)
    ).toEqual(["Kimi K3 High"])
  })

  it("selects a model row with that model's default effort", () => {
    const { onSelectionChange } = openPicker()
    openModelPane()

    fireEvent.click(screen.getByRole("option", { name: "Kimi K3 High" }))

    expect(onSelectionChange).toHaveBeenCalledWith({
      modelId: "fireworks:accounts/fireworks/models/kimi-k3",
      effort: "high",
    })
    expect(screen.queryByTestId("model-picker-panel")).toBeNull()
  })

  it("disables models without image support when images are attached", () => {
    const { onSelectionChange } = openPicker({ requireImageSupport: true })
    openModelPane()

    const kimi = screen.getByRole("option", { name: "Kimi K3 High" })
    expect(kimi).toHaveProperty("disabled", true)

    fireEvent.click(kimi)
    expect(onSelectionChange).not.toHaveBeenCalled()
  })

  it("opens the model pane with ArrowRight and returns with ArrowLeft", () => {
    const { panel } = openPicker()

    fireEvent.keyDown(panel, { key: "ArrowRight" })
    expect(screen.getByRole("listbox", { name: "Models" })).toBeTruthy()

    fireEvent.keyDown(panel, { key: "ArrowLeft" })
    expect(screen.queryByRole("listbox", { name: "Models" })).toBeNull()
  })

  it("moves reasoning focus with the arrow keys and applies it on Enter", () => {
    const { onSelectionChange, panel } = openPicker()

    fireEvent.keyDown(panel, { key: "ArrowUp" })
    fireEvent.keyDown(panel, { key: "Enter" })

    expect(onSelectionChange).toHaveBeenCalledWith({
      modelId: "openai:gpt-5.6-sol",
      effort: "medium",
    })
  })

  it("closes on Escape", () => {
    openPicker()

    fireEvent.keyDown(screen.getByTestId("model-picker-panel"), {
      key: "Escape",
    })

    expect(screen.queryByTestId("model-picker-panel")).toBeNull()
  })

  it("returns to the main pane when Escape closes the model pane", () => {
    const { panel } = openPicker()
    openModelPane()

    fireEvent.keyDown(panel, { key: "Escape" })

    expect(screen.queryByRole("listbox", { name: "Models" })).toBeNull()
    expect(screen.getByTestId("model-picker-panel")).toBeTruthy()
  })
})
