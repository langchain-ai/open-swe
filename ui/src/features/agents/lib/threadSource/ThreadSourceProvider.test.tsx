// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { ThreadSourceProvider, useThreadSource } from "./ThreadSourceProvider"

vi.mock("./useAgentStreamSource", () => ({
  useAgentStreamSource: () => ({ kind: "stream" }),
}))
vi.mock("./useTranscriptSource", () => ({
  useTranscriptSource: () => ({ kind: "transcript" }),
}))

afterEach(() => {
  cleanup()
  window.localStorage.clear()
})

it.each(["agent_workspace", "coding"] as const)(
  "keeps %s v2 on the event log despite an old SDK preference, with streaming for legacy threads",
  (experience) => {
    function Conversation() {
      return <div>{useThreadSource().kind}</div>
    }
    window.localStorage.setItem("open-swe.streaming.use-stream", "true")
    const view = (transcript: boolean) => (
      <ThreadSourceProvider
        threadId="thread"
        transcript={transcript}
        experience={experience}
      >
        <Conversation />
      </ThreadSourceProvider>
    )
    const rendered = render(view(true))
    expect(screen.getByText("transcript")).toBeTruthy()
    rendered.rerender(view(false))
    expect(screen.getByText("stream")).toBeTruthy()
  }
)
