import { describe, expect, it } from "vitest"

import { parseAppServerOptions } from "./app-options.js"

const UI = "/build/ui/index.mjs"

describe("parseAppServerOptions", () => {
  it("resolves paths against the working directory", () => {
    expect(
      parseAppServerOptions(
        ["--port", "3100", "--state-dir", "state", "--ui-entrypoint", UI],
        {},
        "/work"
      )
    ).toEqual({
      port: 3100,
      stateDirectory: "/work/state",
      uiEntrypoint: UI,
    })
  })

  it("rejects a port outside the valid range", () => {
    expect(() =>
      parseAppServerOptions(["--ui-entrypoint", UI], {}, "/work")
    ).not.toThrow()
    expect(() =>
      parseAppServerOptions(["--port", "0", "--ui-entrypoint", UI], {}, "/work")
    ).toThrow("--port must be an integer between 1 and 65535")
  })

  it("takes the graph entrypoint from the environment", () => {
    expect(
      parseAppServerOptions(
        ["--ui-entrypoint", UI],
        { OPEN_SWE_LOCAL_GRAPH_ENTRYPOINT: "tests/e2e/desktop-agent.ts" },
        "/work"
      ).graphEntrypoint
    ).toBe("/work/tests/e2e/desktop-agent.ts")
  })

  it("prefers the flag over the environment", () => {
    expect(
      parseAppServerOptions(
        ["--ui-entrypoint", UI, "--graph-entrypoint", "/flag/agent.ts"],
        { OPEN_SWE_LOCAL_GRAPH_ENTRYPOINT: "/env/agent.ts" },
        "/work"
      ).graphEntrypoint
    ).toBe("/flag/agent.ts")
  })
})
