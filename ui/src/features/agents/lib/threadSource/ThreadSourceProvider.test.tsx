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

const hydrate = vi.hoisted(() => vi.fn(() => Promise.resolve()))

vi.mock("./useAgentStreamSource", () => ({
  useAgentStreamSource: () => ({ kind: "stream", stream: { hydrate } }),
}))
vi.mock("./useTranscriptSource", () => ({
  useTranscriptSource: () => ({ kind: "transcript" }),
}))

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  hydrate.mockClear()
})

it("reads a stream source's thread again in place when refreshed", () => {
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
  expect(hydrate).toHaveBeenCalledOnce()
})

it("opens a recorded thread through SDK streaming by default, and switches it to the transcript and back without changing its UI", () => {
  function Conversation() {
    return <div>{useThreadSource().kind}</div>
  }
  const view = (transcript: boolean) => (
    <ThreadSourceProvider threadId="thread" transcript={transcript}>
      <Conversation />
    </ThreadSourceProvider>
  )
  const rendered = render(view(true))
  expect(screen.getByText("stream")).toBeTruthy()
  act(() => setUseStreamPreference(false))
  expect(screen.getByText("transcript")).toBeTruthy()
  act(() => setUseStreamPreference(true))
  expect(screen.getByText("stream")).toBeTruthy()
  act(() => setUseStreamPreference(false))
  rendered.rerender(view(false))
  expect(screen.getByText("stream")).toBeTruthy()
})
