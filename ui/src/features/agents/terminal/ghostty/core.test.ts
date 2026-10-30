import { readFileSync } from "node:fs"
import { afterAll, describe, expect, it, vi } from "vitest"
import { GhosttyTerminalCore } from "./core"

vi.stubGlobal("fetch", async (url: string) => {
  const name = url.includes("ghostty-write-pty")
    ? "ghostty-write-pty.wasm"
    : "ghostty-vt.wasm"
  return new Response(
    readFileSync(new URL(`./vendor/${name}`, import.meta.url))
  )
})

afterAll(() => vi.unstubAllGlobals())

describe("Ghostty render snapshots", () => {
  it("refreshes cached default colors without text damage", async () => {
    const white = { r: 255, g: 255, b: 255 }
    const slate = { r: 30, g: 41, b: 59 }
    const core = await GhosttyTerminalCore.create(
      20,
      4,
      8,
      16,
      { foreground: white, background: white, cursor: white },
      () => {}
    )
    try {
      core.write("shell output\u001b[2;1H\u001b[31;44mcolored\u001b[0m")
      const initial = core.snapshot()
      const explicit = initial.rowData[1]?.cells[0]
      core.write("\u001b]11;#1e293b\u0007")
      const changed = core.snapshot()
      expect(changed.background).toEqual(slate)
      expect([...changed.dirtyRows]).toEqual([0, 1, 2, 3])
      expect(changed.rowData[0]?.text).toBe("shell output")
      expect(changed.rowData[0]?.cells[0]?.background).toEqual(slate)
      expect(changed.rowData[3]?.cells[0]?.background).toEqual(slate)
      expect(changed.rowData[1]?.cells[0]?.background).toEqual(
        explicit?.background
      )
    } finally {
      core.dispose()
    }
  })
})
