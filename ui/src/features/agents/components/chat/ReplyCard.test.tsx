import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { ReplyCard } from "./ReplyCard"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"

const chunk: ToolExecutionChunk = {
  kind: "tool-execution",
  toolCallId: "reply",
  title: "slack_reply",
  toolKind: "slack",
  status: "completed",
}

describe("ReplyCard", () => {
  it.each([undefined, ["Mark ready"]])(
    "renders Markdown reply links with options %j",
    (options) => {
      const html = renderToStaticMarkup(
        <ReplyCard
          chunk={{
            ...chunk,
            input: {
              message: "[Draft PR](https://example.com/pull/3869)",
              options,
            },
          }}
        />
      )
      expect(html).toContain('href="https://example.com/pull/3869"')
      expect(html).toContain(">Draft PR</a>")
      expect(html).not.toContain("[Draft PR]")
      expect(html.includes("Mark ready")).toBe(Boolean(options))
    }
  )

  it("keeps explicit Slack mrkdwn blocks in their native format", () => {
    const html = renderToStaticMarkup(
      <ReplyCard
        chunk={{
          ...chunk,
          input: {
            message: "fallback",
            blocks: [
              {
                type: "section",
                text: {
                  type: "mrkdwn",
                  text: "<https://example.com/docs|docs> with *bold*",
                },
              },
            ],
          },
        }}
      />
    )
    expect(html).toContain('href="https://example.com/docs"')
    expect(html).toContain("<strong>bold</strong>")
  })
})
