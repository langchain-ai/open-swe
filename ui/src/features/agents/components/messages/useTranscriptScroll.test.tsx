/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import type { LoadEarlier } from "./types"

let useScroll: typeof import("./useTranscriptScroll").useTranscriptScroll
const STORAGE_KEY = "open-swe.transcript-positions.v1"
let resize = () => {}

class TranscriptFixture {
  heights = [300, 300, 300, 300, 300, 300]
  first = 0
  private tops = new WeakMap<HTMLElement, number>()

  attach = (el: HTMLDivElement | null) => {
    if (!el) return
    Object.defineProperties(el, {
      clientHeight: { configurable: true, get: () => 400 },
      scrollHeight: {
        configurable: true,
        get: () =>
          this.heights
            .slice(this.first)
            .reduce((sum, height) => sum + height, 0),
      },
      scrollTop: {
        configurable: true,
        get: () => this.tops.get(el) ?? 0,
        set: (value: number) => {
          this.tops.set(
            el,
            Math.max(0, Math.min(value, el.scrollHeight - el.clientHeight))
          )
        },
      },
    })
  }

  measure(message: HTMLDivElement | null, index: number) {
    if (!message) return
    message.getBoundingClientRect = () => {
      const el = message.closest<HTMLElement>('[data-testid="transcript"]')!
      const y =
        this.heights
          .slice(this.first, index)
          .reduce((sum, height) => sum + height, 0) - el.scrollTop
      return new DOMRect(0, y, 500, this.heights[index])
    }
  }

  scroll(top: number) {
    const el = screen.getByTestId("transcript")
    el.scrollTop = top
    fireEvent.scroll(el)
    return el
  }
}

function Transcript({
  fixture,
  scrollKey = "thread-a",
  loadEarlier,
}: {
  fixture: TranscriptFixture
  scrollKey?: string
  loadEarlier?: LoadEarlier | null
}) {
  const messages = fixture.heights.slice(fixture.first)
  const { scrollRef, contentRef, reserveRef, scrollToBottom } = useScroll({
    scrollKey,
    messages,
    isStreaming: false,
    loadEarlier,
  })
  return (
    <>
      <div ref={scrollRef} data-testid="transcript">
        <div ref={contentRef}>
          {messages.map((height, index) => (
            <div
              key={fixture.first + index}
              ref={(node) => fixture.measure(node, fixture.first + index)}
              data-message-id={`message-${fixture.first + index}`}
              style={{ height }}
            />
          ))}
        </div>
        <div ref={reserveRef} />
        <div
          ref={(node) =>
            fixture.attach(node?.parentElement as HTMLDivElement | null)
          }
        />
      </div>
      <button onClick={scrollToBottom}>Latest</button>
    </>
  )
}

beforeEach(async () => {
  vi.resetModules()
  window.localStorage.clear()
  useScroll = (await import("./useTranscriptScroll")).useTranscriptScroll
  vi.stubGlobal("CSS", { escape: (value: string) => value })
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) {
        resize = callback
      }
      observe() {}
      disconnect() {}
    }
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("restores a reading location after reload and reflow rather than its old pixel offset", () => {
  window.localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify([
      [
        "restored-thread",
        {
          top: 650,
          atBottom: false,
          anchor: { messageId: "message-2", offset: -50 },
        },
      ],
    ])
  )
  const fixture = new TranscriptFixture()
  fixture.heights[0] = 600
  render(<Transcript fixture={fixture} scrollKey="restored-thread" />)
  expect(screen.getByTestId("transcript").scrollTop).toBe(950)
  fixture.heights[0] += 150
  act(() => resize())
  expect(screen.getByTestId("transcript").scrollTop).toBe(1100)
})

it("keeps different threads independent and persists the final position on pagehide", () => {
  const fixture = new TranscriptFixture()
  const view = render(
    <Transcript fixture={fixture} scrollKey="independent-a" />
  )
  const el = fixture.scroll(650)
  fireEvent(window, new Event("pagehide"))
  expect(window.localStorage.getItem(STORAGE_KEY)).toContain("independent-a")
  view.rerender(<Transcript fixture={fixture} scrollKey="independent-b" />)
  expect(el.scrollTop).toBe(1400)
  fixture.scroll(100)
  view.rerender(<Transcript fixture={fixture} scrollKey="independent-a" />)
  expect(el.scrollTop).toBe(650)
})

it("follows new content only when the reader left at the bottom", () => {
  const fixture = new TranscriptFixture()
  const view = render(
    <Transcript fixture={fixture} scrollKey="bottom-thread" />
  )
  fixture.scroll(1400)
  view.rerender(
    <Transcript fixture={fixture} scrollKey="other-bottom-thread" />
  )
  fixture.heights.push(300)
  view.rerender(<Transcript fixture={fixture} scrollKey="bottom-thread" />)
  expect(screen.getByTestId("transcript").scrollTop).toBe(1700)
  fixture.scroll(650)
  fixture.heights.push(300)
  act(() => resize())
  expect(screen.getByTestId("transcript").scrollTop).toBe(650)
  fireEvent.click(screen.getByText("Latest"))
  expect(screen.getByTestId("transcript").scrollTop).toBe(2000)
})

it("loads older history before restoring an anchor outside the current window", () => {
  const fixture = new TranscriptFixture()
  const view = render(<Transcript fixture={fixture} scrollKey="older-thread" />)
  fixture.scroll(350)
  view.unmount()
  fixture.first = 3
  const load = vi.fn()
  const later = render(
    <Transcript
      fixture={fixture}
      scrollKey="older-thread"
      loadEarlier={{ loading: false, onLoadEarlier: load }}
    />
  )
  expect(load).toHaveBeenCalledOnce()
  later.rerender(
    <Transcript
      fixture={fixture}
      scrollKey="older-thread"
      loadEarlier={{ loading: true, onLoadEarlier: load }}
    />
  )
  later.rerender(
    <Transcript
      fixture={fixture}
      scrollKey="older-thread"
      loadEarlier={{ loading: false, onLoadEarlier: load }}
    />
  )
  expect(load).toHaveBeenCalledOnce()
  fixture.first = 0
  later.rerender(<Transcript fixture={fixture} scrollKey="older-thread" />)
  expect(screen.getByTestId("transcript").scrollTop).toBe(350)
})
