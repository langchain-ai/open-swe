/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { useEffect, useLayoutEffect, useMemo, useState } from "react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

let useScroll: typeof import("./useTranscriptScroll").useTranscriptScroll
const STORAGE_KEY = "open-swe.transcript-positions.v1"
let resize = () => {}

class TranscriptFixture {
  heights = [300, 300, 300, 300, 300, 300]
  private tops = new WeakMap<HTMLElement, number>()

  attach = (el: HTMLDivElement | null) => {
    if (!el) return
    Object.defineProperties(el, {
      clientHeight: { configurable: true, get: () => 400 },
      scrollHeight: {
        configurable: true,
        get: () => this.heights.reduce((sum, height) => sum + height, 0),
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
        this.heights.slice(0, index).reduce((sum, height) => sum + height, 0) -
        el.scrollTop
      return new DOMRect(0, y, 500, this.heights[index])
    }
  }
}

function Transcript({ fixture }: { fixture: TranscriptFixture }) {
  const messages = fixture.heights
  const { scrollRef, contentRef, reserveRef } = useScroll({
    scrollKey: "restored-thread",
    messages,
    isStreaming: false,
  })
  return (
    <div ref={scrollRef} data-testid="transcript">
      <div ref={contentRef}>
        {messages.map((height, index) => (
          <div
            key={index}
            ref={(node) => fixture.measure(node, index)}
            data-message-id={`message-${index}`}
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
  )
}

let windowed: {
  fixture: TranscriptFixture
  capture?: () => void
  revealAll?: () => void
}

function WindowedTranscript({
  heights,
  start,
}: {
  heights: Array<number>
  start: number
}) {
  const [from, setFrom] = useState(start)
  const shown = useMemo(() => heights.slice(from), [from, heights])
  const { fixture } = windowed
  useLayoutEffect(() => {
    fixture.heights = shown
  }, [fixture, shown])
  const { scrollRef, contentRef, reserveRef, capturePrependAnchor } = useScroll(
    {
      scrollKey: "restored-thread",
      messages: shown,
      isStreaming: false,
      loadEarlier:
        from > 0
          ? {
              loading: false,
              onLoadEarlier: () => setFrom(from - 1),
              onLoadAll: () => setFrom(0),
            }
          : null,
    }
  )
  useEffect(() => {
    windowed.capture = capturePrependAnchor
    windowed.revealAll = () => setFrom(0)
  }, [capturePrependAnchor])
  return (
    <div ref={scrollRef} data-testid="transcript">
      <div ref={contentRef}>
        {shown.map((height, index) => (
          <div
            key={from + index}
            ref={(node) => fixture.measure(node, index)}
            data-message-id={`message-${from + index}`}
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
  render(<Transcript fixture={fixture} />)
  expect(screen.getByTestId("transcript").scrollTop).toBe(950)
  fixture.heights[0] += 150
  act(() => resize())
  expect(screen.getByTestId("transcript").scrollTop).toBe(1100)
})

it("restores a reading location older than the shown turns by showing every turn the source holds", () => {
  window.localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify([
      [
        "restored-thread",
        {
          top: 650,
          atBottom: false,
          anchor: { messageId: "message-1", offset: -50 },
        },
      ],
    ])
  )
  windowed = { fixture: new TranscriptFixture() }
  render(
    <WindowedTranscript heights={[300, 300, 300, 300, 300, 300]} start={4} />
  )
  expect(screen.getByTestId("transcript").scrollTop).toBe(350)
})

it("leaves the reader in place when content grows after an older page that only grew a folded stretch", () => {
  windowed = { fixture: new TranscriptFixture() }
  render(<WindowedTranscript heights={[0, 0, 300, 300, 300, 300]} start={2} />)
  const transcript = screen.getByTestId("transcript")
  fireEvent.wheel(transcript)
  transcript.scrollTop = 200
  fireEvent.scroll(transcript)
  act(() => {
    windowed.capture?.()
    windowed.revealAll?.()
  })
  expect(transcript.scrollTop).toBe(200)
  windowed.fixture.heights[5] = 800
  act(() => resize())
  expect(transcript.scrollTop).toBe(200)
})
