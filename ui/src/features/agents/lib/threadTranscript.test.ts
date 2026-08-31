import { describe, expect, it } from "vitest"

import { transcriptToUiMessages } from "./threadTranscript"

describe("transcriptToUiMessages", () => {
  it("renders a snapshot's turns through the live-stream pipeline", () => {
    const messages = transcriptToUiMessages({
      available: true,
      hasMore: false,
      messages: [
        { type: "human", content: "fix the build", id: "m1" },
        { type: "ai", content: "on it", id: "m2" },
      ],
    })

    expect(messages.map((message) => message.author)).toEqual(["user", "agent"])
    expect(messages[0]?.chunks[0]).toMatchObject({
      kind: "text",
      text: "fix the build",
    })
  })

  it("carries tool calls through as tool-execution chunks", () => {
    const messages = transcriptToUiMessages({
      available: true,
      hasMore: false,
      messages: [
        { type: "human", content: "read it", id: "m1" },
        {
          type: "ai",
          content: "",
          id: "m2",
          tool_calls: [
            { id: "call-1", name: "read_file", args: { file_path: "a.ts" } },
          ],
        },
        {
          type: "tool",
          content: "contents",
          id: "m3",
          tool_call_id: "call-1",
        },
      ],
    })

    const chunks = messages.flatMap((message) => message.chunks)
    expect(
      chunks.some(
        (chunk) => chunk.kind === "tool-execution" && chunk.toolKind === "read"
      )
    ).toBe(true)
  })

  it("returns nothing when the server could not read the transcript", () => {
    expect(
      transcriptToUiMessages({ available: false, hasMore: false, messages: [] })
    ).toEqual([])
    expect(transcriptToUiMessages(undefined)).toEqual([])
  })

  it("falls back to empty rather than throwing on an unreadable shape", () => {
    expect(
      transcriptToUiMessages({
        available: true,
        hasMore: false,
        messages: [{ nonsense: true }],
      })
    ).toEqual([])
  })
})
