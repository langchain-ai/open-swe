/** @vitest-environment jsdom */

import { act } from "react"
import { flushSync } from "react-dom"
import { createRoot } from "react-dom/client"
import { bench, describe } from "vitest"
import { cleanup, render } from "@testing-library/react"
import { Messages } from "./Messages"
import type { Root } from "react-dom/client"

import type { ThreadStreamFixture } from "@/features/agents/lib/threadStreamFixture"

import {
  buildThreadFixture,
  streamTicks,
} from "@/features/agents/lib/threadStreamFixture"
import {
  createUiMessageProjector,
  streamMessagesToUi,
} from "@/features/agents/lib/streamMessagesToUi"

const actEnvironment = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
actEnvironment.IS_REACT_ACT_ENVIRONMENT = true

const OPTIONS = { warmupIterations: 1, iterations: 5, time: 0, warmupTime: 0 }

function convert(fixture: ThreadStreamFixture) {
  return streamMessagesToUi(fixture.messages, fixture.toolCalls)
}

describe("streamMessagesToUi", () => {
  const fixture = buildThreadFixture(120)

  bench(
    "full rebuild, 120 turns",
    () => {
      convert(fixture)
    },
    OPTIONS
  )
})

for (const turns of [30, 120]) {
  describe(`Messages, ${turns}-turn thread`, () => {
    const fixture = buildThreadFixture(turns)
    const ticks = streamTicks(fixture, 20)

    bench(
      "mount",
      () => {
        render(
          <Messages
            messages={convert(fixture)}
            isStreaming={false}
            streamIsLoading={false}
          />
        )
        cleanup()
      },
      OPTIONS
    )

    // The synchronous first render only — the tail window a reader sees
    // before the rest of the thread mounts in the follow-up transition.
    bench(
      "mount: first paint",
      () => {
        const actEnv = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
        actEnv.IS_REACT_ACT_ENVIRONMENT = false
        const container = document.createElement("div")
        document.body.appendChild(container)
        const paintRoot = createRoot(container)
        flushSync(() => {
          paintRoot.render(
            <Messages
              messages={convert(fixture)}
              isStreaming={false}
              streamIsLoading={false}
            />
          )
        })
        paintRoot.unmount()
        container.remove()
        actEnv.IS_REACT_ACT_ENVIRONMENT = true
      },
      OPTIONS
    )

    // One persistent root (outside RTL's cleanup registry): each iteration
    // replays 20 stream flushes against the mounted thread, the shape of the
    // work the UI does continuously while an agent streams. Conversion goes
    // through the same incremental projector the thread views use.
    const project = createUiMessageProjector()
    let root: Root | null = null
    const renderInto = (fx: ThreadStreamFixture) => {
      if (!root) {
        const container = document.createElement("div")
        document.body.appendChild(container)
        root = createRoot(container)
      }
      const mounted = root
      act(() => {
        mounted.render(
          <Messages
            messages={project(fx.messages, fx.toolCalls)}
            isStreaming
            streamIsLoading
          />
        )
      })
    }

    bench(
      "20 stream flushes: convert + rerender",
      () => {
        renderInto(fixture)
        for (const tick of ticks) renderInto(tick)
      },
      OPTIONS
    )
  })
}
