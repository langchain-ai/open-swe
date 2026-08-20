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
})
