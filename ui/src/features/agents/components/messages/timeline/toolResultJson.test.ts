import { describe, expect, it } from "vitest"

import { formatJsonToolResult } from "./toolResultJson"

describe("formatJsonToolResult", () => {
  it("pretty-prints JSON objects and arrays", () => {
    expect(formatJsonToolResult('{"answer":42}')).toBe('{\n  "answer": 42\n}')
    expect(formatJsonToolResult('["one",2]')).toBe('[\n  "one",\n  2\n]')
  })

  it("ignores non-JSON and values that do not begin with an object or array", () => {
    expect(formatJsonToolResult("{not json}")).toBeNull()
    expect(formatJsonToolResult('  {"answer":42}')).toBeNull()
    expect(formatJsonToolResult('"value"')).toBeNull()
  })

  it("ignores values at or above one MiB", () => {
    const value = `{"value":"${"x".repeat(1024 * 1024)}"}`

    expect(formatJsonToolResult(value)).toBeNull()
  })

  it("measures the UTF-8 byte size", () => {
    const value = `{"value":"${"é".repeat(600_000)}"}`

    expect(value.length).toBeLessThan(1024 * 1024)
    expect(formatJsonToolResult(value)).toBeNull()
  })
})
