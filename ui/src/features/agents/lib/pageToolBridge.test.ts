// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest"

import { PageModelContext } from "@/lib/pageTools"
import { agentsApi } from "./api"
import { PageToolBridge } from "./pageToolBridge"

vi.mock("./api", () => ({
  agentsApi: { recordPageCommand: vi.fn() },
}))

const record = vi.mocked(agentsApi.recordPageCommand)

describe("PageToolBridge", () => {
  beforeEach(() => {
    record.mockReset().mockResolvedValue(undefined)
  })

  it("carries out its session's commands in order, once each", async () => {
    const context = new PageModelContext()
    const shown: Array<string> = []
    await context.registerTool({
      name: "show_code",
      description: "Show code",
      inputSchema: { type: "object", properties: { path: { type: "string" } } },
      execute: (input) => {
        shown.push(String(input["path"]))
        return { shown: input["path"] }
      },
    })
    const bridge = PageToolBridge.of(context)
    const first = {
      position: 1,
      tool: "show_code",
      arguments: { path: "a.py" },
    }
    const second = { position: 2, tool: "gone", arguments: {} }

    await bridge.run([first])
    // A refetch that still lists the first command alongside the next.
    await bridge.run([first, second])

    expect(shown).toEqual(["a.py"])
    expect(record.mock.calls).toEqual([
      [bridge.session, 1, { shown: "a.py" }],
      [
        bridge.session,
        2,
        {
          error:
            "The page no longer offers gone; what is on screen changed since it was queued.",
        },
      ],
    ])
  })
})
