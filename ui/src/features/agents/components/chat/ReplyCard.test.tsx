/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ReplyCard } from "./ReplyCard"

afterEach(() => cleanup())

describe("ReplyCard", () => {
  it("does not claim a reply rejected for newer Slack messages was sent", () => {
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
            output: JSON.stringify({
              success: false,
              error: "new_slack_messages",
            }),
          }}
        />
      </QueryClientProvider>
    )

    expect(
      screen.getByText("Not sent: new Slack messages arrived")
    ).toBeTruthy()
    expect(screen.queryByText("Sent to Slack")).toBeNull()
  })
})
