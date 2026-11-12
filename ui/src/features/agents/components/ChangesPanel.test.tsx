/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { cleanup, render, screen } from "@testing-library/react"
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ChangesPanel, changesEmptyLabel } from "./ChangesPanel"

vi.mock("./DiffFilesView", () => ({
  DiffFilesView: ({ actions }: { actions: React.ReactNode }) => actions,
}))

afterEach(cleanup)

it.each([
  [
    "https://github.com/langchain-ai/langchainplus/pull/60679",
    "/agents/reviews/langchain-ai/langchainplus/60679",
  ],
  ["https://example.com/custom-pr", "https://example.com/custom-pr"],
])("opens the PR destination for %s", async (url, destination) => {
  const router = createRouter({
    routeTree: createRootRoute({
      component: () => (
        <ChangesPanel
          files={[]}
          isLoading={false}
          isFetching={false}
          fullScreen={false}
          onRefresh={() => {}}
          scope="working-tree"
          onScopeChange={() => {}}
          branchScopeAvailable={false}
          pr={{
            url,
            number: 60679,
            title: "Fix PR links",
            state: "open",
            headRef: "fix-pr-links",
            baseRef: "main",
          }}
        />
      ),
    }),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  })
  await router.load()
  render(
    <TooltipProvider>
      <RouterProvider router={router} />
    </TooltipProvider>
  )
  expect(
    screen.getByRole("link", { name: "View PR" }).getAttribute("href")
  ).toBe(destination)
})

describe("changesEmptyLabel", () => {
  it("distinguishes loading, missing, error, and empty changes", () => {
    expect(changesEmptyLabel({ isLoading: true })).toBe("Reading changes…")
    expect(changesEmptyLabel({ isLoading: false, status: "missing" })).toBe(
      "Changes are not available for this workspace."
    )
    expect(changesEmptyLabel({ isLoading: false, status: "error" })).toBe(
      "Could not read changes. Try refreshing."
    )
    expect(changesEmptyLabel({ isLoading: false, status: "ready" })).toBe(
      "No changes yet."
    )
  })

  it("surfaces request errors", () => {
    expect(
      changesEmptyLabel({
        isLoading: false,
        error: new Error("Sandbox unavailable"),
      })
    ).toBe("Sandbox unavailable")
  })
})
