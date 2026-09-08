/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { RepoSelector } from "./RepoSelector"

afterEach(() => cleanup())

function renderSelector() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RepoSelector
        repos={[
          { full_name: "langchain-ai/langchain" },
          { full_name: "langchain-ai/open-swe" },
        ]}
        onRepoChange={vi.fn()}
      />
    </QueryClientProvider>
  )
  fireEvent.click(screen.getByRole("button", { name: /Select repository/ }))
}

describe("RepoSelector", () => {
  it("matches repository names across separators", () => {
    renderSelector()

    fireEvent.change(screen.getByPlaceholderText("Search repositories…"), {
      target: { value: "openswe" },
    })

    expect(screen.getByText("langchain-ai/open-swe")).toBeTruthy()
    expect(screen.queryByText("langchain-ai/langchain")).toBeNull()
  })

  it("matches repository names with fuzzy character gaps", () => {
    renderSelector()

    fireEvent.change(screen.getByPlaceholderText("Search repositories…"), {
      target: { value: "opnswe" },
    })

    expect(screen.getByText("langchain-ai/open-swe")).toBeTruthy()
  })

  it("does not match every repository for separator-only queries", () => {
    renderSelector()

    fireEvent.change(screen.getByPlaceholderText("Search repositories…"), {
      target: { value: "-" },
    })

    expect(screen.getByText("No matches")).toBeTruthy()
    expect(screen.queryByText("langchain-ai/open-swe")).toBeNull()
    expect(screen.queryByText("langchain-ai/langchain")).toBeNull()
  })
})
