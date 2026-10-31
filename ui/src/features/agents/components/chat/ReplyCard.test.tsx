/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ReplyCard } from "./ReplyCard"

afterEach(() => cleanup())

describe("ReplyCard", () => {
  it.each([false, true])(
    "links the delivered message (content blocks: %s)",
    (blocks) => {
      const result = JSON.stringify({
        success: true,
        message_url: "https://slack.com/archives/C123/p1793303440018049",
      })
      render(
        <QueryClientProvider client={new QueryClient()}>
          <ReplyCard
            chunk={{
              kind: "tool-execution",
              toolCallId: "reply-link",
              title: "Reply",
              toolKind: "slack",
              status: "completed",
              input: { message: "Fixed" },
              output: blocks
                ? JSON.stringify([{ type: "text", text: result }])
                : result,
            }}
          />
        </QueryClientProvider>
      )
      expect(
        screen.getByRole("link", { name: "Sent to Slack" }).getAttribute("href")
      ).toBe("https://slack.com/archives/C123/p1793303440018049")
    }
  )

  it.each([
    { status: "pending", output: undefined },
    { status: "error", output: undefined },
    { status: "completed", output: undefined },
    { status: "completed", output: JSON.stringify({ success: false }) },
    {
      status: "completed",
      output: JSON.stringify({ message_url: "javascript:alert(1)" }),
    },
  ] as const)(
    "does not link an unavailable destination: %j",
    ({ status, output }) => {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <ReplyCard
            chunk={{
              kind: "tool-execution",
              toolCallId: "reply-unavailable",
              title: "Reply",
              toolKind: "slack",
              status,
              input: { message: "Update" },
              output,
            }}
          />
        </QueryClientProvider>
      )
      expect(screen.queryByRole("link")).toBeNull()
    }
  )

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
