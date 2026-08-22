import path from "node:path"

import { describe, expect, it } from "vitest"

import { parseLocalServerOptions } from "./server-options.js"

describe("parseLocalServerOptions", () => {
  it("parses an explicit loopback server and state directory", () => {
    expect(
      parseLocalServerOptions(
        ["--host", "127.0.0.1", "--port", "49152", "--state-dir", "state"],
        "/application"
      )
    ).toEqual({
      host: "127.0.0.1",
      port: 49_152,
      stateDirectory: path.resolve("/application/state"),
    })
  })

  it("rejects an invalid port", () => {
    expect(() => parseLocalServerOptions(["--port", "0"])).toThrow(
      "--port must be an integer"
    )
  })
})
