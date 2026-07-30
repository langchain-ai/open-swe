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
    id: "google_genai:gemini-3.6-flash",
    label: "Gemini 3.6 Flash",
    efforts: ["minimal", "low", "medium", "high"],
    default_effort: "medium",
    supports_images: true,
  },
  {
    id: "fireworks:accounts/fireworks/models/kimi-k3-code",
    label: "Kimi K3",
    efforts: ["low", "medium", "high"],
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
  fireEvent.click(screen.getByRole("button", { name: /GPT-5.6 Sol High/ }))
  return { onSelectionChange, panel: screen.getByTestId("model-picker-panel") }
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

  it("lists every model with its effort and filters on search", () => {
    openPicker()

    const models = screen.getByRole("listbox", { name: "Models" })
    expect(
      within(models)
        .getAllByRole("option")
        .map((option) => option.textContent)
    ).toEqual(["GPT-5.6 Sol High", "Gemini 3.6 Flash Medium", "Kimi K3 High"])

    fireEvent.change(screen.getByLabelText("Search models"), {
      target: { value: "kimi" },
    })

    expect(
      within(screen.getByRole("listbox", { name: "Models" }))
        .getAllByRole("option")
        .map((option) => option.textContent)
    ).toEqual(["Kimi K3 High"])
  })

  it("shows the context window and reasoning options for the focused model", () => {
    openPicker()

    const panel = screen.getByTestId("model-picker-panel")
    expect(panel.textContent).toContain("Context")
    expect(panel.textContent).toContain("272.0K")

    expect(
      within(screen.getByRole("listbox", { name: "Reasoning effort" }))
        .getAllByRole("option")
        .map((option) => option.textContent)
    ).toEqual(["None", "Low", "Medium", "High", "Extra High"])

    fireEvent.mouseEnter(
      screen.getByRole("option", { name: "Gemini 3.6 Flash Medium" })
    )

    expect(
      within(screen.getByRole("listbox", { name: "Reasoning effort" }))
        .getAllByRole("option")
        .map((option) => option.textContent)
    ).toEqual(["Minimal", "Low", "Medium", "High"])
  })

  it("omits the context section for models without a context window", () => {
    openPicker()

    fireEvent.mouseEnter(
      screen.getByRole("option", { name: "Gemini 3.6 Flash Medium" })
    )

    expect(screen.getByTestId("model-picker-panel").textContent).not.toContain(
      "Context"
    )
  })

  it("selects a reasoning effort for the focused model", () => {
    const { onSelectionChange } = openPicker()

    fireEvent.mouseEnter(
      screen.getByRole("option", { name: "Gemini 3.6 Flash Medium" })
    )
    fireEvent.click(
      within(
        screen.getByRole("listbox", { name: "Reasoning effort" })
      ).getByRole("option", { name: "Low" })
    )

    expect(onSelectionChange).toHaveBeenCalledWith({
      modelId: "google_genai:gemini-3.6-flash",
      effort: "low",
    })
    expect(screen.queryByTestId("model-picker-panel")).toBeNull()
  })

  it("selects a model row with that model's default effort", () => {
    const { onSelectionChange } = openPicker()

    fireEvent.click(screen.getByRole("option", { name: "Kimi K3 High" }))

    expect(onSelectionChange).toHaveBeenCalledWith({
      modelId: "fireworks:accounts/fireworks/models/kimi-k3-code",
      effort: "high",
    })
  })

  it("disables models without image support when images are attached", () => {
    const { onSelectionChange } = openPicker({ requireImageSupport: true })

    const kimi = screen.getByRole("option", { name: "Kimi K3 High" })
    expect(kimi).toHaveProperty("disabled", true)

    fireEvent.click(kimi)
    expect(onSelectionChange).not.toHaveBeenCalled()
  })

  it("moves model focus and effort with the arrow keys", () => {
    const { onSelectionChange } = openPicker()
    const panel = screen.getByTestId("model-picker-panel")

    fireEvent.keyDown(panel, { key: "ArrowDown" })
    expect(
      within(screen.getByRole("listbox", { name: "Reasoning effort" }))
        .getAllByRole("option")
        .map((option) => option.textContent)
    ).toEqual(["Minimal", "Low", "Medium", "High"])

    fireEvent.keyDown(panel, { key: "ArrowRight" })
    fireEvent.keyDown(panel, { key: "ArrowDown" })

    expect(onSelectionChange).toHaveBeenCalledWith({
      modelId: "google_genai:gemini-3.6-flash",
      effort: "high",
    })
    expect(screen.getByTestId("model-picker-panel")).toBeTruthy()
  })

  it("closes on Escape", () => {
    openPicker()

    fireEvent.keyDown(screen.getByTestId("model-picker-panel"), {
      key: "Escape",
    })

    expect(screen.queryByTestId("model-picker-panel")).toBeNull()
  })
})
