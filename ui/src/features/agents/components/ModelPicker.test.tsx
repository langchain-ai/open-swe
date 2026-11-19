/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ModelPicker } from "./ModelPicker"
import type { ModelOption } from "@/lib/api"

afterEach(() => cleanup())

const MODELS: ModelOption[] = [
  {
    id: "openai:gpt-6.1-sol",
    label: "GPT-6.1 Sol",
    efforts: ["low", "high"],
    default_effort: "high",
    supports_images: true,
    context_window: 272_000,
  },
  {
    id: "google_genai:gemini-4-carbon",
    label: "Gemini 4 Carbon",
    efforts: ["low", "medium"],
    default_effort: "medium",
    supports_images: true,
  },
  {
    id: "fireworks:accounts/fireworks/models/kimi-k3",
    label: "Kimi K3",
    efforts: ["high"],
    default_effort: "high",
    supports_images: false,
  },
]

function renderPicker(
  props: Partial<React.ComponentProps<typeof ModelPicker>> = {}
) {
  const onSelectionChange = vi.fn()
  render(
    <ModelPicker
      models={MODELS}
      selection={{ modelId: "openai:gpt-6.1-sol", effort: "high" }}
      onSelectionChange={onSelectionChange}
      {...props}
    />
  )
  const trigger = screen.getByRole("combobox", { name: /^Model$/ })
  return { onSelectionChange, trigger }
}

describe("ModelPicker", () => {
  it("uses the new model’s default effort when switching models", async () => {
    const { onSelectionChange, trigger } = renderPicker()
    fireEvent.click(trigger)
    fireEvent.click(
      await screen.findByRole("option", { name: "Gemini 4 Carbon" })
    )

    expect(onSelectionChange).toHaveBeenCalledWith({
      modelId: "google_genai:gemini-4-carbon",
      effort: "medium",
    })
  })

  it("shows the routing tier and leaves reasoning to Auto", async () => {
    const { trigger } = renderPicker({
      selection: null,
      routed: { route: "fast" },
    })
    expect(trigger.textContent).toContain("Auto Fast")
    expect(
      screen.queryByRole("combobox", { name: "Reasoning effort" })
    ).toBeNull()

    fireEvent.click(trigger)
    expect(
      (await screen.findByRole("option", { name: "Auto Fast" })).textContent
    ).toBe("Auto Fast")
    expect(
      screen.getByRole("option", { name: "GPT-6.1 Sol" }).textContent
    ).toBe("GPT-6.1 Sol")
  })

  it("clears the explicit selection when choosing Auto", async () => {
    const { onSelectionChange, trigger } = renderPicker()
    fireEvent.click(trigger)
    fireEvent.click(await screen.findByRole("option", { name: /^Auto/ }))

    expect(onSelectionChange).toHaveBeenCalledWith(null)
  })

  it("changes reasoning without changing the selected model", async () => {
    const { onSelectionChange } = renderPicker()
    fireEvent.click(screen.getByRole("combobox", { name: "Reasoning effort" }))
    fireEvent.click(await screen.findByRole("option", { name: "Low" }))

    expect(onSelectionChange).toHaveBeenCalledWith({
      modelId: "openai:gpt-6.1-sol",
      effort: "low",
    })
  })

  it("keeps models that cannot accept an attached image unavailable", async () => {
    const { onSelectionChange, trigger } = renderPicker({
      requireImageSupport: true,
    })
    fireEvent.click(trigger)
    const kimi = await screen.findByRole("option", { name: /Kimi K3/ })
    expect(kimi.getAttribute("aria-disabled")).toBe("true")

    fireEvent.click(kimi)
    expect(onSelectionChange).not.toHaveBeenCalled()
  })
})
