/** @vitest-environment jsdom */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ModelPairControl } from "./WorkspaceSettingsSections"

afterEach(cleanup)

describe("ModelPairControl", () => {
  it("preserves saved effort while a subagent uses the parent model", () => {
    const onChange = vi.fn()
    const props = {
      label: "Robin Agent subagents",
      models: [
        {
          id: "openai:gpt-6.1-sol",
          label: "GPT-6.1 Sol",
          supports_images: true,
          efforts: ["medium", "high"],
          default_effort: "medium",
        },
      ],
      model: "openai:gpt-6.1-sol",
      effort: "high",
      onChange,
      disabled: false,
    }
    const { rerender } = render(
      <ModelPairControl {...props} parentModel={props.model} />
    )
    const effort = () =>
      screen.getByRole("combobox", {
        name: "Robin Agent subagents reasoning effort",
      }) as HTMLButtonElement
    expect(effort().disabled).toBe(true)
    expect(effort().textContent).toContain("Parent run")
    rerender(<ModelPairControl {...props} parentModel="another-model" />)
    expect(effort().disabled).toBe(false)
    expect(effort().textContent).toContain("high")
    expect(onChange).not.toHaveBeenCalled()
  })
})

it("keeps the saved model label visible after opening and dismissing its dropdown", async () => {
  render(
    <ModelPairControl
      label="Robin Agent"
      models={[
        {
          id: "openai:gpt-6.1-sol",
          label: "GPT-6.1 Sol",
          supports_images: true,
          efforts: ["medium"],
          default_effort: "medium",
        },
      ]}
      model="openai:gpt-6.1-sol"
      effort="medium"
      onChange={vi.fn()}
      disabled={false}
    />
  )
  const trigger = screen.getByRole("combobox", { name: "Robin Agent model" })
  expect(trigger.textContent).toContain("GPT-6.1 Sol")
  fireEvent.click(trigger)
  expect(
    await screen.findByRole("option", { name: "GPT-6.1 Sol" })
  ).toBeTruthy()
  fireEvent.keyDown(trigger, { key: "Escape" })
  await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull())
  expect(trigger.textContent).toContain("GPT-6.1 Sol")
})
