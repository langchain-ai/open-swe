/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"

import { RepoSelector } from "./RepoSelector"

vi.mock("@/lib/profile", () => ({
  useRefreshRepos: () => ({ isPending: false, mutate: vi.fn() }),
}))

vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { controlling_org: "org" } }),
}))

afterEach(cleanup)

it("never offers archived choices or an archive toggle by default", async () => {
  render(
    <RepoSelector
      repos={[
        { full_name: "org/active", private: false, archived: false },
        { full_name: "other/active", private: false, archived: false },
        { full_name: "legacy", private: false, archived: true },
      ]}
      onRepoChange={vi.fn()}
    />,
    { wrapper: TooltipProvider }
  )
  fireEvent.click(screen.getByRole("button", { name: "Select repository" }))
  expect(await screen.findByText("active")).toBeTruthy()
  expect(screen.getByText("other/active")).toBeTruthy()
  expect(screen.queryByText("legacy")).toBeNull()
  expect(screen.queryByRole("checkbox", { name: "Show archived" })).toBeNull()
})

it("hides archives even in search until opted in, without clearing the selection", async () => {
  const onRepoChange = vi.fn()
  render(
    <RepoSelector
      allowArchived
      repos={[
        { full_name: "org/active", private: false, archived: false },
        { full_name: "legacy", private: false, archived: true },
        { full_name: "org/internal-legacy", private: true, archived: true },
      ]}
      selectedRepo="org/legacy"
      onRepoChange={onRepoChange}
    />,
    { wrapper: TooltipProvider }
  )
  fireEvent.click(screen.getByRole("button", { name: "legacy" }))
  expect(
    await screen.findByRole("button", { name: /active\s*Public/ })
  ).toBeTruthy()
  expect(
    screen.queryByRole("button", { name: /legacy\s*Public\s*archive/ })
  ).toBeNull()
  expect(onRepoChange).not.toHaveBeenCalled()

  fireEvent.change(screen.getByPlaceholderText("Search repositories…"), {
    target: { value: "LEGACY" },
  })
  expect(screen.getByText("No matches")).toBeTruthy()
  fireEvent.click(screen.getByRole("checkbox", { name: "Show archived" }))
  expect(
    screen.getByRole("button", { name: /legacy\s*Public\s*archive/ })
  ).toBeTruthy()
  expect(screen.queryByRole("button", { name: /active\s*Public/ })).toBeNull()
  fireEvent.click(
    screen.getByRole("button", {
      name: /internal-legacy\s*Private\s*archive/,
    })
  )
  expect(onRepoChange).toHaveBeenCalledWith("org/internal-legacy")
})
