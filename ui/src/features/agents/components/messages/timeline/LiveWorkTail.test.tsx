/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { WorkTail, buildRenderItems } from "../renderItems"
import { LiveWorkTail } from "./LiveWorkTail"
import type { Chunk } from "@/features/agents/lib/types"

afterEach(() => cleanup())

const tools: Array<Chunk> = Array.from({ length: 8 }, (_, index) => ({
  kind: "tool-execution",
  toolCallId: `read-${index}`,
  title: "read_file",
  toolKind: "read",
  status: "completed",
}))

function tailWithThought(text: string): WorkTail {
  return new WorkTail(buildRenderItems([...tools, { kind: "reasoning", text }]))
}

describe("LiveWorkTail", () => {
  it("keeps its full height when a streaming thought starts a shorter paragraph", () => {
    const longParagraph = "Now I'm reading the queue store. ".repeat(6)
    const filled = tailWithThought(longParagraph)
    expect(filled.previous).not.toBeNull()

    const { rerender } = render(<LiveWorkTail tail={filled} />, {
      wrapper: TooltipProvider,
    })
    const group = screen.getByRole("group", { name: "Recent activity" })
    expect(group.style.height).toBe("12.5rem")

    const shorter = tailWithThought(`${longParagraph}\n\nOne more thing.`)
    expect(shorter.previous).toBeNull()
    rerender(<LiveWorkTail tail={shorter} />)

    expect(group.style.height).toBe("12.5rem")
  })
})
