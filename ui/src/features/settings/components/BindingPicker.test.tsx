/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"

import { BindingPicker, type PickerItem } from "./BindingPicker"
import { RepositoryPicker } from "./RoutingBindingPickers"

vi.mock("@/lib/profile", () => ({
  useRepos: () => ({
    data: {
      repositories: [
        { full_name: "org/public-sdk", private: false },
        { full_name: "org/internal-sdk", private: true },
        { full_name: "org/public-docs", private: false },
        { full_name: "org/public-archive", private: false, archived: true },
        { full_name: "org/private-archive", private: true, archived: true },
      ],
    },
  }),
}))

afterEach(cleanup)

const ITEMS: Array<PickerItem> = [
  { id: "C1", label: "#oss-help", meta: "public · 10 members" },
  { id: "C2", label: "#oss-maintainers", meta: "private · 4 members" },
  { id: "C3", label: "#commits" },
  {
    id: "C4",
    label: "#community-announce",
    warning: "Robin is not in this channel",
  },
]

function renderPicker(
  overrides: Partial<Parameters<typeof BindingPicker>[0]> = {}
) {
  const onChange = vi.fn()
  render(
    <BindingPicker
      triggerLabel="Choose channels"
      title="Slack channels"
      noun="channel"
      pluralNoun="channels"
      items={ITEMS}
      selected={["C1"]}
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
    />,
    { wrapper: TooltipProvider }
  )
  fireEvent.click(screen.getByRole("button", { name: "Choose channels" }))
  return { onChange }
}

describe("BindingPicker", () => {
  it("filters repositories alongside search without losing selections", async () => {
    const onChange = vi.fn()
    render(
      <RepositoryPicker
        selected={["org/public-archive"]}
        onChange={onChange}
      />,
      { wrapper: TooltipProvider }
    )
    fireEvent.click(screen.getByRole("button", { name: "Choose repositories" }))
    expect(await screen.findByText("Available · 3")).toBeTruthy()
    expect(screen.getByText("Public archive")).toBeTruthy()
    expect(
      screen.queryByRole("checkbox", { name: "private-archive" })
    ).toBeNull()
    fireEvent.click(screen.getByRole("checkbox", { name: "public-archive" }))
    expect(
      screen.queryByRole("checkbox", { name: "public-archive" })
    ).toBeNull()
    fireEvent.click(screen.getByRole("switch", { name: "Exclude archived" }))
    expect(screen.getByText("Public archive")).toBeTruthy()
    expect(screen.getByText("Private archive")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Public" }))
    expect(
      screen.getByRole("checkbox", { name: "public-archive" })
    ).toBeTruthy()
    expect(
      screen.queryByRole("checkbox", { name: "private-archive" })
    ).toBeNull()
    expect(screen.queryByRole("checkbox", { name: "internal-sdk" })).toBeNull()
    fireEvent.click(screen.getByRole("checkbox", { name: "public-sdk" }))
    fireEvent.click(screen.getByRole("button", { name: "Internal" }))
    expect(
      screen.getByRole("checkbox", { name: "private-archive" })
    ).toBeTruthy()
    expect(
      screen.queryByRole("checkbox", { name: "public-archive" })
    ).toBeNull()
    expect(
      screen
        .getByRole("checkbox", { name: "public-sdk" })
        .getAttribute("aria-checked")
    ).toBe("true")
    expect(screen.getByRole("checkbox", { name: "internal-sdk" })).toBeTruthy()
    expect(screen.queryByRole("checkbox", { name: "public-docs" })).toBeNull()
    fireEvent.change(screen.getByLabelText("Search repositories"), {
      target: { value: "ORG Public_Docs" },
    })
    expect(screen.queryByRole("checkbox", { name: "internal-sdk" })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "All" }))
    expect(screen.getByRole("checkbox", { name: "public-docs" })).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Save 1 repository" }))
    expect(onChange).toHaveBeenCalledWith(["org/public-sdk"])
  })

  it("reveals filtered rows when the filter is switched off and saves the draft", async () => {
    const { onChange } = renderPicker()

    fireEvent.click(
      await screen.findByRole("switch", { name: "Only channels the bot is in" })
    )
    const revealed = screen.getByRole("checkbox", {
      name: "#community-announce",
    })
    expect(screen.getByText("Robin is not in this channel")).toBeTruthy()
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
    expect(screen.getByText("Selected · 2")).toBeTruthy()
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
    expect(screen.getByText("Selected · 1")).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: "Save 1 channel" }))
    expect(onChange).toHaveBeenCalledWith(["C0999"])
  })

  it("still lists a selected id the directory does not know", async () => {
    renderPicker({ selected: ["C1", "C0OLD"] })

    expect(await screen.findByRole("checkbox", { name: "C0OLD" })).toBeTruthy()
    expect(screen.getByText("Selected · 2")).toBeTruthy()
  })
})
