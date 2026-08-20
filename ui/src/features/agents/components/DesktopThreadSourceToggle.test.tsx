/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { DesktopThreadSourceToggle } from "./DesktopThreadSourceToggle"

afterEach(() => cleanup())

describe("DesktopThreadSourceToggle", () => {
  it("exposes the selected source and switches sources", () => {
    const onSourceChange = vi.fn()
    render(
      <DesktopThreadSourceToggle
        source="local"
        localCount={4}
        cloudCount={7}
        localActivity={{ running: 1, completed: 2 }}
        cloudActivity={{ running: 3, completed: 1 }}
        onSourceChange={onSourceChange}
      />
    )

    const cloud = screen.getByRole("button", {
      name: "Cloud threads, 7, 3 running, 1 completed",
    })
    const local = screen.getByRole("button", {
      name: "This Mac threads, 4, 1 running, 2 completed",
    })
    expect(cloud.getAttribute("aria-pressed")).toBe("false")
    expect(local.getAttribute("aria-pressed")).toBe("true")

    fireEvent.click(cloud)

    expect(onSourceChange).toHaveBeenCalledWith("cloud")
  })
})
