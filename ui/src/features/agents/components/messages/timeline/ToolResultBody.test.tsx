/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ToolResultBody } from "./ToolResultBody"
import { ShellEntryBody } from "./entryBodies"

vi.mock("@/features/agents/components/chat/CodeBlock", () => ({
  CodeBlock: ({ text, language }: { text: string; language?: string }) => (
    <code data-language={language}>{text}</code>
  ),
}))

afterEach(() => cleanup())

describe("ToolResultBody", () => {
  it("shows eval input separately from its console output and hides a redundant undefined result", () => {
    render(
      <ShellEntryBody
        chunk={{
          kind: "tool-execution",
          toolCallId: "eval-1",
          title: "Eval",
          toolKind: "code",
          status: "completed",
          input: { code: "console.log(42)" },
          output: "<stdout>\n42\n</stdout>\n<result>undefined</result>",
        }}
      />
    )

    expect(screen.getByText("console.log(42)").dataset.language).toBe(
      "javascript"
    )
    expect(screen.getByText("Console")).toBeTruthy()
    expect(screen.getByText("42")).toBeTruthy()
    expect(screen.queryByText("undefined")).toBeNull()
  })

  it("renders JSON through the syntax-highlighted code path", () => {
    render(<ToolResultBody value='{"answer":42}' />)

    expect(screen.getByText(/"answer": 42/).dataset.language).toBe("json")
  })

  it("keeps invalid JSON as plain text", () => {
    const { container } = render(<ToolResultBody value="{not json}" />)

    expect(container.querySelector("pre")?.textContent).toBe("{not json}")
  })
})
