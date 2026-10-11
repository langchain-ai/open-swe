/** @vitest-environment jsdom */

import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ThinkingSpinner } from "./ThinkingSpinner"

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

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

  it("keeps elapsed seconds across activity changes and resets after stopping", () => {
    vi.useFakeTimers()
    const { rerender } = render(<ThinkingSpinner isActive />)
    expect(screen.getByText("0s")).toBeTruthy()
    act(() => vi.advanceTimersByTime(3000))
    rerender(<ThinkingSpinner isActive label="Exploring…" />)
    expect(screen.getByText("3s")).toBeTruthy()
    rerender(<ThinkingSpinner isActive={false} />)
    expect(vi.getTimerCount()).toBe(0)
    rerender(<ThinkingSpinner isActive />)
    expect(screen.getByText("0s")).toBeTruthy()
  })

  it("prioritizes sandbox setup status", () => {
    render(
      <ThinkingSpinner
        isActive
        settingUpSandbox
        label="Exploring · AgentTurn.tsx"
      />
    )

    expect(
      screen.getByText("Agent is setting up the environment…")
    ).toBeTruthy()
    expect(screen.queryByText("Exploring · AgentTurn.tsx")).toBeNull()
  })
})
