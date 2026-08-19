/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"

describe("SandboxedHtmlFrame", () => {
  afterEach(cleanup)

  it("defaults to an opaque sandbox without delegated capabilities", () => {
    render(<SandboxedHtmlFrame html="<p>Safe</p>" title="Safe output" />)

    const frame = screen.getByTitle("Safe output")
    expect(frame.getAttribute("sandbox")).toBe("")
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer")
    expect(frame.hasAttribute("allow")).toBe(false)
  })

  it("supports explicit capabilities for interactive output", () => {
    render(
      <SandboxedHtmlFrame
        html="<script>document.body.textContent = 'Interactive'</script>"
        title="Interactive output"
        sandbox="allow-scripts allow-downloads"
        allow="clipboard-write"
      />
    )

    const frame = screen.getByTitle("Interactive output")
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-downloads")
    expect(frame.getAttribute("allow")).toBe("clipboard-write")
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer")
  })
})
