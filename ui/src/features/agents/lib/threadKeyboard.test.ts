/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest"

import type { SidebarThreadItem } from "./sidebarThreads"
import {
  adjacentThreadRow,
  orderThreadRows,
  threadRangeKeys,
} from "./threadKeyboard"
import type { RegisteredThreadRow } from "./threadKeyboard"

function row(key: string, node: HTMLElement): RegisteredThreadRow {
  return {
    item: { key } as SidebarThreadItem,
    node,
  }
}

describe("thread keyboard navigation", () => {
  it("uses connected DOM order", () => {
    const first = document.createElement("div")
    const second = document.createElement("div")
    const detached = document.createElement("div")
    document.body.append(first, second)

    expect(
      orderThreadRows([
        row("second", second),
        row("detached", detached),
        row("first", first),
      ]).map((entry) => entry.item.key)
    ).toEqual(["first", "second"])
  })

  it("stops at boundaries and starts from the corresponding edge", () => {
    const rows = [
      row("first", document.createElement("div")),
      row("second", document.createElement("div")),
    ]

    expect(adjacentThreadRow(rows, undefined, 1)?.item.key).toBe("first")
    expect(adjacentThreadRow(rows, undefined, -1)?.item.key).toBe("second")
    expect(adjacentThreadRow(rows, "first", -1)).toBeUndefined()
    expect(adjacentThreadRow(rows, "second", 1)).toBeUndefined()
  })

  it("selects an inclusive range in either direction", () => {
    const rows = ["one", "two", "three"].map((key) =>
      row(key, document.createElement("div"))
    )

    expect([...threadRangeKeys(rows, "three", "one")]).toEqual([
      "one",
      "two",
      "three",
    ])
  })
})
