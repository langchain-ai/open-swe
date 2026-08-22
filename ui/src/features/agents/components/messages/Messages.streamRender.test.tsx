/** @vitest-environment jsdom */

import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { Messages } from "./Messages"
import type * as renderItemsModule from "./renderItems"
import { createUiMessageProjector } from "@/features/agents/lib/streamMessagesToUi"
import {
  buildThreadFixture,
  streamTicks,
} from "@/features/agents/lib/threadStreamFixture"

const counters = vi.hoisted(() => ({ buildRenderItems: 0 }))

// `buildRenderItems` runs when a turn's chunks change identity, so its call
// count is the number of turns that actually re-derived their timeline.
vi.mock(
  "@/features/agents/components/messages/renderItems",
  async (importOriginal) => {
    const actual = await importOriginal<typeof renderItemsModule>()
    return {
      ...actual,
      buildRenderItems: (
        ...args: Parameters<typeof actual.buildRenderItems>
      ) => {
        counters.buildRenderItems += 1
        return actual.buildRenderItems(...args)
      },
    }
  }
)

afterEach(() => {
  cleanup()
  counters.buildRenderItems = 0
})

describe("Messages under stream flushes", () => {
  it("re-derives only the streaming turn's timeline per flush", () => {
    const fixture = buildThreadFixture(20)
    const project = createUiMessageProjector()
    const view = render(
      <Messages
        messages={project(fixture.messages, fixture.toolCalls)}
        isStreaming
        streamIsLoading
      />
    )
    expect(counters.buildRenderItems).toBe(20)

    counters.buildRenderItems = 0
    for (const tick of streamTicks(fixture, 3)) {
      view.rerender(
        <Messages
          messages={project(tick.messages, tick.toolCalls)}
          isStreaming
          streamIsLoading
        />
      )
    }
    expect(counters.buildRenderItems).toBe(3)
  })
})
