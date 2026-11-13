// @vitest-environment jsdom
import { cleanup, render, screen, act } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { useState } from "react"

import { setUseStreamPreference } from "@/lib/streamPreference"
import {
  ThreadSourceProvider,
  useThreadSource,
  useRefreshThreadSource,
} from "./ThreadSourceProvider"

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

it("preserves submitted review state when refreshing a stream source", () => {
  function Review() {
    const [submitted, setSubmitted] = useState(false)
    const refresh = useRefreshThreadSource()
    return (
      <button
        disabled={submitted}
        onClick={() => {
          setSubmitted(true)
          refresh?.()
        }}
      >
        {submitted ? "Submitted" : "Submit to Robin"}
      </button>
    )
  }
  render(
    <ThreadSourceProvider threadId="thread" transcript={false}>
      <Review />
    </ThreadSourceProvider>
  )
  act(() => screen.getByRole("button", { name: "Submit to Robin" }).click())
  expect(
    (screen.getByRole("button", { name: "Submitted" }) as HTMLButtonElement)
      .disabled
  ).toBe(true)
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
