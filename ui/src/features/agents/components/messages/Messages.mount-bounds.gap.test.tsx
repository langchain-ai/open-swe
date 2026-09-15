/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { Messages } from "./Messages"
import type { Message } from "@/features/agents/lib/types"

vi.mock("@/features/agents/components/WorkflowApprovalCard", () => ({
  WorkflowApprovalCard: () => null,
}))

afterEach(() => cleanup())

describe("Messages transcript mount budget", () => {
  it("mounts the live tail without mounting an entire long transcript", () => {
    const messages: Array<Message> = Array.from(
      { length: 1_200 },
      (_, index) => ({
        id: `user-${index}`,
        author: "user",
        timestamp: new Date(1_700_000_000_000 + index).toISOString(),
        chunks: [{ kind: "text", text: `Message ${index}` }],
      })
    )

    render(<Messages messages={messages} isStreaming={false} />)

    expect(screen.getByText("Message 1199")).toBeTruthy()
    expect(screen.queryByText("Message 0")).toBeNull()
    expect(screen.getAllByTestId("user-message").length).toBeLessThanOrEqual(
      500
    )
  })
})
