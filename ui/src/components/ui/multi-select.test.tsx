/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { useState } from "react"

import { MultiSelect } from "./multi-select"

const options = [
  "acme/app",
  "acme/other",
  "globex/api",
  "globex/web",
  "initech/tps",
] as const

function Harness({ onChange }: { onChange?: (value: string[]) => void }) {
  const [value, setValue] = useState<string[]>([])
  return (
    <MultiSelect
      label="Filter by repository"
      placeholder="All repositories"
      searchPlaceholder="Search repositories…"
      options={options}
      value={value}
      onValueChange={(next) => {
        setValue(next)
        onChange?.(next)
      }}
    />
  )
}

const optionLabels = () =>
  screen.getAllByRole("menuitemcheckbox").map((item) => item.textContent)

afterEach(cleanup)

describe("MultiSelect search", () => {
  it("keeps typed characters and narrows the options", async () => {
    render(<Harness />)
    fireEvent.click(screen.getByLabelText("Filter by repository"))
    const input = await screen.findByLabelText("Search repositories…")
    // autoFocus does not settle reliably under jsdom, so take the focus the
    // browser would have given the input before testing that typing keeps it.
    ;(input as HTMLInputElement).focus()
    for (const key of ["g", "l", "o", "b"]) {
      fireEvent.keyDown(input, { key })
      fireEvent.change(input, {
        target: { value: `${(input as HTMLInputElement).value}${key}` },
      })
      fireEvent.keyUp(input, { key })
    }
    expect((input as HTMLInputElement).value).toBe("glob")
    expect(document.activeElement).toBe(input)
    expect(optionLabels()).toEqual(["globex/api", "globex/web"])
  })

  it("matches case-insensitively and reports an empty result", async () => {
    render(<Harness />)
    fireEvent.click(screen.getByLabelText("Filter by repository"))
    const input = await screen.findByLabelText("Search repositories…")
    fireEvent.change(input, { target: { value: "ACME" } })
    expect(optionLabels()).toEqual(["acme/app", "acme/other"])
    fireEvent.change(input, { target: { value: "nope" } })
    expect(screen.queryAllByRole("menuitemcheckbox")).toEqual([])
    expect(screen.getByText("No matches")).toBeTruthy()
  })

  it("selects a filtered option and clears the selection", async () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    fireEvent.click(screen.getByLabelText("Filter by repository"))
    const input = await screen.findByLabelText("Search repositories…")
    fireEvent.change(input, { target: { value: "globex/web" } })
    fireEvent.click(
      screen.getByRole("menuitemcheckbox", { name: "globex/web" })
    )
    expect(onChange).toHaveBeenLastCalledWith(["globex/web"])
    fireEvent.click(screen.getByRole("menuitem", { name: "Clear selection" }))
    expect(onChange).toHaveBeenLastCalledWith([])
  })
})
