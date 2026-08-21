/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ThinkingSpinner } from "./ThinkingSpinner"

afterEach(() => cleanup())

describe("ThinkingSpinner", () => {
  it("shows one live activity label and disappears when work settles", () => {
    const { rerender } = render(
      <ThinkingSpinner isActive label="Exploring · AgentTurn.tsx" />
    )

    expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite")
    expect(screen.getByRole("status").getAttribute("aria-atomic")).toBe("true")
    expect(screen.getByText("Exploring · AgentTurn.tsx")).toBeTruthy()

    rerender(
      <ThinkingSpinner isActive={false} label="Exploring · AgentTurn.tsx" />
    )

    expect(screen.queryByText("Exploring · AgentTurn.tsx")).toBeNull()
  })

  it("prioritizes sandbox setup status", () => {
    render(
      <ThinkingSpinner
        isActive
        settingUpSandbox
        label="Exploring · AgentTurn.tsx"
      />
    )

    expect(screen.getByText("Setting up sandbox…")).toBeTruthy()
    expect(screen.queryByText("Exploring · AgentTurn.tsx")).toBeNull()
  })
})
