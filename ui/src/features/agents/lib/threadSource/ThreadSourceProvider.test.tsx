// @vitest-environment jsdom
import { cleanup, render, screen, act } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { setUseStreamPreference } from "@/lib/streamPreference"
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

it("switches a recorded thread to SDK streaming and back without changing its UI", () => {
  function Conversation() {
    return <div>{useThreadSource().kind}</div>
  }
  const view = (transcript: boolean) => (
    <ThreadSourceProvider threadId="thread" transcript={transcript}>
      <Conversation />
    </ThreadSourceProvider>
  )
  const rendered = render(view(true))
  expect(screen.getByText("transcript")).toBeTruthy()
  act(() => setUseStreamPreference(true))
  expect(screen.getByText("stream")).toBeTruthy()
  act(() => setUseStreamPreference(false))
  expect(screen.getByText("transcript")).toBeTruthy()
  rendered.rerender(view(false))
  expect(screen.getByText("stream")).toBeTruthy()
})
