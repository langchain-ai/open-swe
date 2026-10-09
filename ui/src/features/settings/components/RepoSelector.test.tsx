/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"

import { RepoSelector } from "./RepoSelector"

vi.mock("@/lib/profile", () => ({
  useRefreshRepos: () => ({ isPending: false, mutate: vi.fn() }),
}))

afterEach(cleanup)

it("never offers archived choices or an archive toggle by default", async () => {
  render(
    <RepoSelector
      repos={[
        { full_name: "org/active", private: false, archived: false },
        { full_name: "org/legacy", private: false, archived: true },
      ]}
      onRepoChange={vi.fn()}
    />,
    { wrapper: TooltipProvider }
  )
  fireEvent.click(screen.getByRole("button", { name: "Select repository" }))
  expect(await screen.findByText("org/active")).toBeTruthy()
  expect(screen.queryByText("org/legacy")).toBeNull()
  expect(screen.queryByRole("checkbox", { name: "Show archived" })).toBeNull()
})

it("hides archives even in search until opted in, without clearing the selection", async () => {
  const onRepoChange = vi.fn()
  render(
    <RepoSelector
      allowArchived
      repos={[
        { full_name: "org/active", private: false, archived: false },
        { full_name: "org/legacy", private: false, archived: true },
        { full_name: "org/internal-legacy", private: true, archived: true },
      ]}
      selectedRepo="org/legacy"
      onRepoChange={onRepoChange}
    />,
    { wrapper: TooltipProvider }
  )
  fireEvent.click(screen.getByRole("button", { name: "org/legacy" }))
  expect(
    await screen.findByRole("button", { name: /org\/active\s*Public/ })
  ).toBeTruthy()
  expect(
    screen.queryByRole("button", { name: /org\/legacy\s*Public\s*archive/ })
  ).toBeNull()
  expect(onRepoChange).not.toHaveBeenCalled()

  fireEvent.change(screen.getByPlaceholderText("Search repositories…"), {
    target: { value: "LEGACY" },
  })
  expect(screen.getByText("No matches")).toBeTruthy()
  fireEvent.click(screen.getByRole("checkbox", { name: "Show archived" }))
  expect(
    screen.getByRole("button", { name: /org\/legacy\s*Public\s*archive/ })
  ).toBeTruthy()
  expect(
    screen.queryByRole("button", { name: /org\/active\s*Public/ })
  ).toBeNull()
  fireEvent.click(
    screen.getByRole("button", {
      name: /org\/internal-legacy\s*Private\s*archive/,
    })
  )
  expect(onRepoChange).toHaveBeenCalledWith("org/internal-legacy")
})
