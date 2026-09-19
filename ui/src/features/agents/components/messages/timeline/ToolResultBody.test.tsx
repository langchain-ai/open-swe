/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ToolResultBody } from "./ToolResultBody"

vi.mock("@/features/agents/components/chat/CodeBlock", () => ({
  CodeBlock: ({ text, language }: { text: string; language?: string }) => (
    <code data-language={language}>{text}</code>
  ),
}))

afterEach(() => cleanup())

describe("ToolResultBody", () => {
  it("renders JSON through the syntax-highlighted code path", () => {
    render(<ToolResultBody value='{"answer":42}' />)

    expect(screen.getByText(/"answer": 42/).dataset.language).toBe("json")
  })

  it("keeps invalid JSON as plain text", () => {
    const { container } = render(<ToolResultBody value="{not json}" />)

    expect(container.querySelector("pre")?.textContent).toBe("{not json}")
  })

  it("renders read_file status headers as line-number gutters", () => {
    const { container } = render(
      <ToolResultBody
        value={
          "@@ lines 8-10 of 20 | next offset 10 @@\nfirst\n  second\nthird"
        }
      />
    )

    expect(container.querySelector("pre")?.textContent).toBe(
      " 8  first\n 9    second\n10  third"
    )
  })

  it("preserves read_file notices above the numbered source", () => {
    const { container } = render(
      <ToolResultBody
        value={
          "[Output was truncated due to size limits.]\n@@ lines 3-4 of 10 | next offset 4 | truncated due to size @@\nalpha\nbeta"
        }
      />
    )

    expect(container.querySelector("pre")?.textContent).toBe(
      "[Output was truncated due to size limits.]\n3  alpha\n4  beta"
    )
  })
})
