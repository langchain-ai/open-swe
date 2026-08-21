/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { WorkEntryRow } from "./WorkEntryRow"
import type { WorkEntryView } from "./workEntry"

afterEach(() => cleanup())

const entry: WorkEntryView = {
  icon: "eye",
  heading: "Read",
  preview: "AGENTS.md",
  tone: "tool",
  status: "completed",
  expandedText: "# Agents\n\nhello",
}

describe("WorkEntryRow", () => {
  it("expands its detail through the collapsible trigger", () => {
    render(<WorkEntryRow entry={entry} />)

    const trigger = screen.getByRole("button", { name: "Read AGENTS.md" })
    expect(trigger.getAttribute("aria-expanded")).toBe("false")
    expect(screen.queryByText(/hello/)).toBeNull()

    fireEvent.click(trigger)

    expect(trigger.getAttribute("aria-expanded")).toBe("true")
    expect(screen.getByText(/hello/).textContent).toBe("# Agents\n\nhello")
  })

  it("renders a plain line when there is nothing to expand", () => {
    render(<WorkEntryRow entry={{ ...entry, expandedText: null }} />)

    expect(screen.queryByRole("button")).toBeNull()
    expect(screen.getByText("Read").textContent).toBe("Read")
  })
})
