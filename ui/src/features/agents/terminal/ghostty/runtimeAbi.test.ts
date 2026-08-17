import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

const pinnedVersion = readFileSync(
  fileURLToPath(
    new URL("../../../../../native/libghostty-vt/VERSION", import.meta.url)
  ),
  "utf8"
).trim()
const wasm = readFileSync(
  fileURLToPath(new URL("./vendor/ghostty-vt.wasm", import.meta.url))
)
const writePtyWasm = readFileSync(
  fileURLToPath(new URL("./vendor/ghostty-write-pty.wasm", import.meta.url))
)

type WasmFunction = (...args: Array<number>) => number

describe("vendored libghostty-vt WebAssembly", () => {
  it("matches the pin and exports its runtime ABI", async () => {
    expect(wasm.byteLength).toBeLessThan(750_000)
    const result = await WebAssembly.instantiate(wasm, {
      env: { log: () => {} },
    })
    const instance =
      result instanceof WebAssembly.Instance ? result : result.instance
    const call = (name: string, ...args: Array<number>) =>
      (instance.exports[name] as WasmFunction)(...args)
    const memory = instance.exports.memory as WebAssembly.Memory
    const output = call("ghostty_wasm_alloc_u8_array", 8)
    expect(call("ghostty_build_info", 10, output)).toBe(0)
    const view = new DataView(memory.buffer, output, 8)
    const revision = new TextDecoder().decode(
      new Uint8Array(
        memory.buffer,
        view.getUint32(0, true),
        view.getUint32(4, true)
      )
    )
    expect(revision).toBe(pinnedVersion)
    const jsonPointer = call("ghostty_type_json")
    const bytes = new Uint8Array(memory.buffer, jsonPointer)
    const layouts = JSON.parse(
      new TextDecoder().decode(bytes.subarray(0, bytes.indexOf(0)))
    ) as Record<string, { size: number }>
    expect(layouts.GhosttyTerminalOptions?.size).toBe(8)
  })

  it("routes terminal replies through the callback trampoline", async () => {
    const result = await WebAssembly.instantiate(wasm, {
      env: { log: () => {} },
    })
    const instance =
      result instanceof WebAssembly.Instance ? result : result.instance
    const memory = instance.exports.memory as WebAssembly.Memory
    let reply = ""
    const trampolineResult = await WebAssembly.instantiate(writePtyWasm, {
      env: {
        t3_write_pty: (
          _terminal: number,
          _userdata: number,
          pointer: number,
          length: number
        ) => {
          reply += new TextDecoder().decode(
            new Uint8Array(memory.buffer, pointer, length)
          )
        },
      },
    })
    const trampoline =
      trampolineResult instanceof WebAssembly.Instance
        ? trampolineResult
        : trampolineResult.instance
    const table = instance.exports
      .__indirect_function_table as WebAssembly.Table
    const callbackIndex = table.length
    table.grow(1)
    table.set(callbackIndex, trampoline.exports.ghostty_write_pty)
    const call = (name: string, ...args: Array<number>) =>
      (instance.exports[name] as WasmFunction)(...args)
    const options = call("ghostty_wasm_alloc_u8_array", 8)
    const optionsView = new DataView(memory.buffer, options, 8)
    optionsView.setUint16(0, 80, true)
    optionsView.setUint16(2, 24, true)
    const terminalSlot = call("ghostty_wasm_alloc_opaque")
    expect(call("ghostty_terminal_new", 0, terminalSlot, options)).toBe(0)
    const terminal = new DataView(memory.buffer).getUint32(terminalSlot, true)
    call("ghostty_terminal_set", terminal, 0, 1)
    call("ghostty_terminal_set", terminal, 1, callbackIndex)
    const query = new TextEncoder().encode("\u001b[5n")
    const pointer = call("ghostty_wasm_alloc_u8_array", query.length)
    new Uint8Array(memory.buffer, pointer, query.length).set(query)
    call("ghostty_terminal_vt_write", terminal, pointer, query.length)
    expect(reply).toBe("\u001b[0n")
  })
})
