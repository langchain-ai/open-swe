/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ToolResultBody } from "./ToolResultBody"
import { ShellEntryBody } from "./entryBodies"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"

vi.mock("@/features/agents/components/chat/CodeBlock", () => ({
  CodeBlock: ({ text, language }: { text: string; language?: string }) => (
    <code data-language={language}>{text}</code>
  ),
}))

afterEach(() => cleanup())

describe("ShellEntryBody", () => {
  it.each([undefined, '{"success":true,"task_id":'])(
    "keeps lazy background output loading for preview %s",
    (output) => {
      const chunk: ToolExecutionChunk = {
        kind: "tool-execution",
        toolCallId: "background-build",
        title: "background_execute",
        toolKind: "background",
        status: "completed",
        input: { command: "pnpm build" },
        output,
        loadOutput: async () => '{"success":true,"task_id":"build"}',
      }
      const { rerender } = render(<ShellEntryBody chunk={chunk} />)

      expect(
        screen.getByText(/Loading (the rest of the )?output…/)
      ).toBeTruthy()
      expect(screen.queryByText("Started in the background.")).toBeNull()

      rerender(
        <ShellEntryBody
          chunk={chunk}
          loadedText='{"success":true,"task_id":"build"}'
        />
      )

      expect(screen.getByText("Started in the background.")).toBeTruthy()
      expect(screen.queryByText(/Loading/)).toBeNull()
      expect(screen.queryByText(/task_id/)).toBeNull()
    }
  )
})

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
