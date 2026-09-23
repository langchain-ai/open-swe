/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { OwnershipPicker, type PickerItem } from "./OwnershipPicker"

afterEach(cleanup)

const ITEMS: Array<PickerItem> = [
  { id: "C1", label: "#oss-help", meta: "public · 10 members" },
  { id: "C2", label: "#oss-maintainers", meta: "private · 4 members" },
  { id: "C3", label: "#commits", owner: { slug: "core", name: "Core" } },
  {
    id: "C4",
    label: "#community-announce",
    warning: "Open SWE is not in this channel",
  },
]

function renderPicker(
  overrides: Partial<Parameters<typeof OwnershipPicker>[0]> = {}
) {
  const onChange = vi.fn()
  render(
    <OwnershipPicker
      triggerLabel="Choose channels"
      title="Slack channels"
      noun="channel"
      pluralNoun="channels"
      items={ITEMS}
      selected={["C1"]}
      workspaceSlug="oss"
      onChange={onChange}
      searchPlaceholder="Search channels"
      filter={{
        label: "Only channels the bot is in",
        matches: (item) => !item.warning,
      }}
      manual={{
        label: "Add a channel by ID",
        placeholder: "C0123456789",
        normalize: (raw) =>
          /^C[0-9A-Z]{2,}$/.test(raw.trim().toUpperCase())
            ? raw.trim().toUpperCase()
            : null,
        invalidHint: "Channel IDs start with C.",
      }}
      {...overrides}
    />
  )
  fireEvent.click(screen.getByRole("button", { name: "Choose channels" }))
  return { onChange }
}

describe("OwnershipPicker", () => {
  it("groups rows by ownership and keeps other workspaces' rows unselectable", async () => {
    renderPicker()

    expect(await screen.findByText("In this workspace · 1")).toBeTruthy()
    expect(screen.getByText("Available · 1")).toBeTruthy()
    expect(screen.getByText("Owned by another workspace · 1")).toBeTruthy()
    const taken = screen.getByRole("checkbox", { name: "#commits" })
    expect(taken.getAttribute("aria-disabled")).toBe("true")
    expect(screen.getByText("Core")).toBeTruthy()
    // The filter hides the channel the bot is not in, and says so.
    expect(
      screen.queryByRole("checkbox", { name: "#community-announce" })
    ).toBeNull()
    expect(screen.getByText("1 hidden by the filter")).toBeTruthy()
  })

  it("reveals filtered rows when the filter is switched off and saves the draft", async () => {
    const { onChange } = renderPicker()

    fireEvent.click(
      await screen.findByRole("switch", { name: "Only channels the bot is in" })
    )
    const revealed = screen.getByRole("checkbox", {
      name: "#community-announce",
    })
    expect(screen.getByText("Open SWE is not in this channel")).toBeTruthy()
    fireEvent.click(revealed)
    fireEvent.click(screen.getByRole("checkbox", { name: "#oss-maintainers" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "#oss-help" }))

    fireEvent.click(screen.getByRole("button", { name: "Save 2 channels" }))

    expect(onChange).toHaveBeenCalledWith(["C4", "C2"])
  })

  it("keeps draft selections visible while searching and discards them on cancel", async () => {
    const { onChange } = renderPicker()

    fireEvent.click(screen.getByRole("checkbox", { name: "#oss-maintainers" }))
    fireEvent.change(await screen.findByLabelText("Search channels"), {
      target: { value: "commits" },
    })

    expect(screen.getByRole("checkbox", { name: "#oss-help" })).toBeTruthy()
    expect(
      screen.getByRole("checkbox", { name: "#oss-maintainers" })
    ).toBeTruthy()
    expect(screen.getByText("In this workspace · 2")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))

    expect(onChange).not.toHaveBeenCalled()
  })

  it("adds an id the directory does not list and rejects a malformed one", async () => {
    const { onChange } = renderPicker({ selected: [] })

    const input = await screen.findByLabelText("Add a channel by ID")
    fireEvent.change(input, { target: { value: "nope" } })
    fireEvent.click(screen.getByRole("button", { name: "Add" }))
    expect(screen.getByRole("alert").textContent).toBe(
      "Channel IDs start with C."
    )

    fireEvent.change(input, { target: { value: "c0999" } })
    fireEvent.click(screen.getByRole("button", { name: "Add" }))
    expect(screen.getByRole("checkbox", { name: "C0999" })).toBeTruthy()
    expect(screen.getByText("In this workspace · 1")).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: "Save 1 channel" }))
    expect(onChange).toHaveBeenCalledWith(["C0999"])
  })

  it("refuses a typed id that another workspace owns", async () => {
    const { onChange } = renderPicker({
      selected: [],
      manual: {
        label: "Add a channel by ID",
        placeholder: "C0123456789",
        normalize: (raw) => raw.trim().toUpperCase(),
        invalidHint: "Channel IDs start with C.",
      },
    })

    fireEvent.change(await screen.findByLabelText("Add a channel by ID"), {
      target: { value: "c3" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Add" }))

    expect(screen.getByRole("alert").textContent).toBe(
      "#commits belongs to Core."
    )
    expect(
      screen
        .getByRole("checkbox", { name: "#commits" })
        .getAttribute("aria-disabled")
    ).toBe("true")
    fireEvent.click(screen.getByRole("button", { name: "Save 0 channels" }))
    expect(onChange).toHaveBeenCalledWith([])
  })

  it("lets a conflicting row that is already selected be removed", async () => {
    const { onChange } = renderPicker({ selected: ["C3"] })

    const stuck = await screen.findByRole("checkbox", { name: "#commits" })
    expect(stuck.getAttribute("aria-disabled")).not.toBe("true")
    fireEvent.click(stuck)
    expect(
      screen
        .getByRole("checkbox", { name: "#commits" })
        .getAttribute("aria-disabled")
    ).toBe("true")
    fireEvent.click(screen.getByRole("button", { name: "Save 0 channels" }))

    expect(onChange).toHaveBeenCalledWith([])
  })

  it("still lists a selected id the directory does not know", async () => {
    renderPicker({ selected: ["C1", "C0OLD"] })

    expect(await screen.findByRole("checkbox", { name: "C0OLD" })).toBeTruthy()
    expect(screen.getByText("In this workspace · 2")).toBeTruthy()
  })
})
