/** @vitest-environment jsdom */

import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { SqlResultTable, parseSqlResult } from "./SqlResultTable"

afterEach(() => cleanup())

describe("SqlResultTable", () => {
  it("renders SQL columns, values, nulls, and truncation metadata", () => {
    render(
      <SqlResultTable
        output={JSON.stringify({
          ok: true,
          columns: ["id", "name", "metadata"],
          rows: [
            [1, "Ada", { role: "admin" }],
            [2, null, ["active"]],
          ],
          row_count: 2,
          truncated: true,
        })}
      />
    )

    expect(screen.getByText("2 rows")).toBeTruthy()
    expect(screen.getByText("Results truncated")).toBeTruthy()
    const table = screen.getByRole("table")
    expect(within(table).getByText("id")).toBeTruthy()
    expect(within(table).getByText("Ada")).toBeTruthy()
    expect(within(table).getByText("NULL")).toBeTruthy()
    expect(within(table).getByText('{"role":"admin"}')).toBeTruthy()
    expect(within(table).getByText('["active"]')).toBeTruthy()
  })

  it("renders an empty result", () => {
    render(
      <SqlResultTable
        output={JSON.stringify({
          ok: true,
          columns: ["id"],
          rows: [],
          row_count: 0,
          truncated: false,
        })}
      />
    )

    expect(screen.getByText("0 rows")).toBeTruthy()
    expect(screen.getByText("No rows")).toBeTruthy()
  })

  it("rejects malformed results", () => {
    expect(
      parseSqlResult(
        JSON.stringify({
          ok: true,
          columns: ["id", "name"],
          rows: [[1]],
          row_count: 1,
          truncated: false,
        })
      )
    ).toBeNull()
  })
})
