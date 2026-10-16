/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ReplyCard } from "./ReplyCard"

afterEach(() => cleanup())

describe("ReplyCard", () => {
  it("shows a rejected reply that carried images as not sent", () => {
    const rejection = JSON.stringify({
      success: false,
      error: "new_slack_messages",
    })
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ReplyCard
          chunk={{
            kind: "tool-execution",
            toolCallId: "reply-1",
            title: "Reply",
            toolKind: "slack",
            status: "completed",
            input: { message: "Fixed in the draft PR" },
            output: JSON.stringify([
              { type: "text", text: rejection },
              { type: "image", base64: "aGk=", mime_type: "image/png" },
            ]),
          }}
        />
      </QueryClientProvider>
    )

    expect(screen.getByText("Failed to send to Slack")).toBeTruthy()
  })
})
