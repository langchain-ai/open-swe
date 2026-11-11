/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import type { ScoreStore } from "./scoreStore"
import { TraceFlight } from "./TraceFlight"

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(document, "hasFocus").mockReturnValue(true)
  // jsdom has no 2D canvas; the loop still runs the sim and the flights without drawing.
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null)
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

it("lands and exits even when the run is refused on the way home, reporting the refusal first", async () => {
  let refuse: (error: unknown) => void = () => {}
  const store: ScoreStore = {
    best: async () => null,
    submit: () =>
      new Promise((_, reject) => {
        refuse = reject
      }),
  }
  const calls: string[] = []
  render(
    <TraceFlight
      seed={42}
      scoreStore={store}
      theme="light"
      reducedMotion={false}
      home={() => new DOMRect(0, 0, 32, 32)}
      onExit={() => calls.push("exit")}
      onRunEnd={(run) => calls.push(`run ended: ${run.endReason}`)}
      onSubmitError={() => calls.push("refused")}
    />
  )
  const game = screen.getByRole("application", { name: "Trace Flight" })
  await act(() => vi.advanceTimersByTimeAsync(1500))
  fireEvent.keyDown(game, { code: "Space" })
  await act(() => vi.advanceTimersByTimeAsync(200))

  fireEvent.keyDown(game, { code: "Escape" })
  await act(async () => refuse(new Error("stale")))
  expect(calls).toEqual(["run ended: quit"])

  await act(() => vi.advanceTimersByTimeAsync(1000))
  expect(calls).toEqual(["run ended: quit", "refused", "exit"])
})

it("turns back on Esc during the leap in, and a held Esc can't cut the flight short", async () => {
  const onExit = vi.fn()
  render(
    <TraceFlight
      seed={42}
      scoreStore={{
        best: async () => null,
        submit: () => new Promise(() => {}),
      }}
      theme="light"
      reducedMotion={false}
      home={() => new DOMRect(0, 0, 32, 32)}
      onExit={onExit}
    />
  )
  const game = screen.getByRole("application", { name: "Trace Flight" })
  await act(() => vi.advanceTimersByTimeAsync(300))

  // The host closes on any Esc the game leaves unclaimed, so each one must be claimed.
  expect(fireEvent.keyDown(game, { code: "Escape" })).toBe(false)
  expect(fireEvent.keyDown(game, { code: "Escape", repeat: true })).toBe(false)
  await act(() => vi.advanceTimersByTimeAsync(100))
  expect(onExit).not.toHaveBeenCalled()

  await act(() => vi.advanceTimersByTimeAsync(1000))
  expect(onExit).toHaveBeenCalledOnce()
})

it("holds still while something else has the keyboard, and carries on when it comes back", async () => {
  const onRunEnd = vi.fn()
  render(
    <>
      <button type="button">Palette</button>
      <TraceFlight
        seed={42}
        scoreStore={{
          best: async () => null,
          submit: () => new Promise(() => {}),
        }}
        theme="light"
        reducedMotion={false}
        home={() => new DOMRect(0, 0, 32, 32)}
        onExit={() => {}}
        onRunEnd={onRunEnd}
      />
    </>
  )
  const game = screen.getByRole("application", { name: "Trace Flight" })
  await act(() => vi.advanceTimersByTimeAsync(1500))
  fireEvent.keyDown(game, { code: "Space" })

  act(() => screen.getByRole("button", { name: "Palette" }).focus())
  await act(() => vi.advanceTimersByTimeAsync(20_000))
  expect(onRunEnd).not.toHaveBeenCalled()

  act(() => game.focus())
  await act(() => vi.advanceTimersByTimeAsync(20_000))
  expect(onRunEnd).toHaveBeenCalled()
})

it("leaves at once on an Esc that comes before the leap has drawn a frame", async () => {
  const onExit = vi.fn()
  render(
    <TraceFlight
      seed={42}
      scoreStore={{
        best: async () => null,
        submit: () => new Promise(() => {}),
      }}
      theme="light"
      reducedMotion={false}
      home={() => new DOMRect(0, 0, 32, 32)}
      onExit={onExit}
    />
  )
  fireEvent.keyDown(screen.getByRole("application", { name: "Trace Flight" }), {
    code: "Escape",
  })
  expect(onExit).toHaveBeenCalledOnce()

  await act(() => vi.advanceTimersByTimeAsync(2000))
  expect(onExit).toHaveBeenCalledOnce()
})

it("shows the run's score even when the run can't be saved", async () => {
  render(
    <TraceFlight
      seed={42}
      scoreStore={{
        best: async () => null,
        submit: () => Promise.reject(new Error("offline")),
      }}
      theme="light"
      reducedMotion={false}
      onExit={() => {}}
    />
  )
  fireEvent.keyDown(screen.getByRole("application", { name: "Trace Flight" }), {
    code: "Space",
  })
  await act(() => vi.advanceTimersByTimeAsync(20_000))

  const card = screen.getByRole("dialog", { name: "Run over" })
  expect(card.textContent).not.toContain("…")
  expect(screen.getByText("Couldn't save this run.")).toBeTruthy()
})
