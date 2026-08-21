/** @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { FileDiff as CoreFileDiff } from "@pierre/diffs"
import type { DiffVirtualizer, RegisteredDiffInstance } from "./diffScroll"
import {
  clampScrollTop,
  elementCenterTarget,
  jumpAndHold,
  scrollCardToTop,
  scrollCardToTopVirtual,
  scrollDiffLineToCenter,
  scrollElementToCenter,
} from "./diffScroll"

function fakeScroller({
  scrollTop = 0,
  scrollHeight = 1000,
  clientHeight = 400,
  top = 0,
}: {
  scrollTop?: number
  scrollHeight?: number
  clientHeight?: number
  top?: number
} = {}) {
  const el = document.createElement("div")
  let current = scrollTop
  Object.defineProperty(el, "scrollTop", {
    get: () => current,
    set: (next: number) => {
      current = next
    },
  })
  Object.defineProperty(el, "scrollHeight", { get: () => scrollHeight })
  Object.defineProperty(el, "clientHeight", { get: () => clientHeight })
  el.getBoundingClientRect = () =>
    ({ top, height: clientHeight }) as unknown as DOMRect
  const scrollTo = vi.fn((options: ScrollToOptions) => {
    if (options.top !== undefined) current = options.top
  })
  el.scrollTo = scrollTo as unknown as HTMLElement["scrollTo"]
  return Object.assign(el, { scrollTo })
}

function fakeElement(top: number, height: number) {
  const el = document.createElement("div")
  el.getBoundingClientRect = () => ({ top, height }) as unknown as DOMRect
  return el
}

class FakeResizeObserver {
  static instances: Array<FakeResizeObserver> = []
  observed: Array<Element> = []
  disconnected = false
  constructor(private callback: ResizeObserverCallback) {
    FakeResizeObserver.instances.push(this)
  }
  observe(target: Element) {
    this.observed.push(target)
  }
  unobserve() {}
  disconnect() {
    this.disconnected = true
  }
  reflow() {
    this.callback([], this as unknown as ResizeObserver)
  }
}

let frames: Array<() => void> = []

function flushFrames() {
  const pending = frames
  frames = []
  for (const frame of pending) frame()
}

beforeEach(() => {
  FakeResizeObserver.instances = []
  frames = []
  vi.stubGlobal("ResizeObserver", FakeResizeObserver)
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => {
    frames.push(cb)
    return frames.length
  })
  vi.stubGlobal("cancelAnimationFrame", (handle: number) => {
    frames[handle - 1] = () => {}
  })
})

afterEach(() => vi.unstubAllGlobals())

describe("clampScrollTop", () => {
  it("keeps a target inside the scrollable range", () => {
    const scroller = fakeScroller()
    expect(clampScrollTop(scroller, 250)).toBe(250)
    expect(clampScrollTop(scroller, 900)).toBe(600)
    expect(clampScrollTop(scroller, -40)).toBe(0)
  })
})

describe("elementCenterTarget", () => {
  it("centers the element in the scroller viewport", () => {
    const scroller = fakeScroller({ scrollTop: 50, top: 100 })
    expect(elementCenterTarget(fakeElement(500, 100), scroller)).toBe(300)
  })

  it("clamps against the bottom of the scroll range", () => {
    const scroller = fakeScroller({ scrollTop: 400, top: 0 })
    expect(elementCenterTarget(fakeElement(900, 100), scroller)).toBe(600)
  })
})

describe("scrollElementToCenter", () => {
  it("scrolls to the centering target and reports the distance moved", () => {
    const scroller = fakeScroller({ scrollTop: 50, top: 100 })
    expect(scrollElementToCenter(fakeElement(500, 100), scroller)).toBe(250)
    expect(scroller.scrollTo).toHaveBeenCalledWith({
      top: 300,
      behavior: "auto",
    })
  })
})

describe("jumpAndHold", () => {
  it("jumps immediately and re-asserts the target when the content reflows", () => {
    const scroller = fakeScroller()
    let target = 120
    jumpAndHold(scroller, () => target)
    expect(scroller.scrollTo).toHaveBeenCalledWith({
      top: 120,
      behavior: "auto",
    })

    target = 260
    FakeResizeObserver.instances[0]!.reflow()
    flushFrames()
    expect(scroller.scrollTo).toHaveBeenLastCalledWith({
      top: 260,
      behavior: "auto",
    })
    expect(scroller.scrollTo).toHaveBeenCalledTimes(2)
  })

  it("observes the scroll content when the scroller has one", () => {
    const scroller = fakeScroller()
    const content = document.createElement("div")
    scroller.append(content)
    jumpAndHold(scroller, () => 10)
    expect(FakeResizeObserver.instances[0]!.observed).toEqual([content])
  })

  it("leaves the scroller alone when it is already within a pixel", () => {
    const scroller = fakeScroller({ scrollTop: 0 })
    jumpAndHold(scroller, () => 100)
    FakeResizeObserver.instances[0]!.reflow()
    flushFrames()
    expect(scroller.scrollTo).toHaveBeenCalledTimes(1)
  })

  it("stops holding as soon as the reader scrolls", () => {
    const scroller = fakeScroller()
    let target = 120
    jumpAndHold(scroller, () => target)
    scroller.dispatchEvent(new Event("wheel"))
    expect(FakeResizeObserver.instances[0]!.disconnected).toBe(true)

    target = 900
    FakeResizeObserver.instances[0]!.reflow()
    flushFrames()
    expect(scroller.scrollTo).toHaveBeenCalledTimes(1)
  })

  it("stops on the returned handle, and stopping twice is harmless", () => {
    const scroller = fakeScroller()
    const stop = jumpAndHold(scroller, () => 300)
    stop()
    stop()
    expect(FakeResizeObserver.instances).toHaveLength(1)
    expect(FakeResizeObserver.instances[0]!.disconnected).toBe(true)
  })

  it("gives up after the hold timeout", () => {
    vi.useFakeTimers()
    try {
      const scroller = fakeScroller()
      jumpAndHold(scroller, () => 300, 700)
      vi.advanceTimersByTime(700)
      expect(FakeResizeObserver.instances[0]!.disconnected).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe("scrollCardToTop", () => {
  it("aligns the card with the top of the scroller", () => {
    const scroller = fakeScroller({ scrollTop: 50, top: 100 })
    scrollCardToTop(fakeElement(500, 100), scroller)
    expect(scroller.scrollTo).toHaveBeenCalledWith({
      top: 450,
      behavior: "auto",
    })
  })

  it("falls back to scrollIntoView without a scroller", () => {
    const el = fakeElement(500, 100)
    el.scrollIntoView = vi.fn()
    scrollCardToTop(el, null)()
    expect(el.scrollIntoView).toHaveBeenCalledWith({ block: "start" })
  })
})

describe("scrollCardToTopVirtual", () => {
  it("uses the virtualizer offset less the top gap", () => {
    const scroller = fakeScroller()
    const virtualizer = {
      getOffsetInScrollContainer: () => 320,
    } as unknown as DiffVirtualizer
    scrollCardToTopVirtual(fakeElement(0, 0), scroller, virtualizer)
    expect(scroller.scrollTo).toHaveBeenCalledWith({
      top: 312,
      behavior: "auto",
    })
  })
})

describe("scrollDiffLineToCenter", () => {
  function target(
    getLinePosition?: () => { top: number; height: number } | undefined
  ): RegisteredDiffInstance<unknown> {
    return {
      host: fakeElement(150, 40),
      instance: (getLinePosition
        ? { getLinePosition }
        : {}) as unknown as CoreFileDiff<unknown>,
    }
  }

  it("centers the line using the diff instance geometry", () => {
    const scroller = fakeScroller({ scrollTop: 40, top: 100 })
    expect(
      scrollDiffLineToCenter(
        target(() => ({ top: 200, height: 20 })),
        12,
        "additions",
        scroller
      )
    ).toBe(true)
    expect(scroller.scrollTo).toHaveBeenCalledWith({
      top: 100,
      behavior: "auto",
    })
  })

  it("reports failure when the instance cannot position lines", () => {
    const scroller = fakeScroller()
    expect(scrollDiffLineToCenter(target(), 12, "additions", scroller)).toBe(
      false
    )
    expect(scroller.scrollTo).not.toHaveBeenCalled()
  })

  it("reports failure when the line has not rendered", () => {
    const scroller = fakeScroller()
    expect(
      scrollDiffLineToCenter(
        target(() => undefined),
        12,
        "additions",
        scroller
      )
    ).toBe(false)
    expect(scroller.scrollTo).not.toHaveBeenCalled()
  })
})
