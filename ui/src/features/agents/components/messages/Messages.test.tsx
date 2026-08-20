/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { Messages } from "./Messages"

afterEach(() => cleanup())

describe("Messages", () => {
  it("shows run activity while a stream is starting with no messages", () => {
    render(<Messages messages={[]} isStreaming />)

    expect(screen.getByRole("status").textContent).toBe("Working…")
  })
})
