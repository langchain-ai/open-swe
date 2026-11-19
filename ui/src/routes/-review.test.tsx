/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { ReviewSettings } from "@/features/settings/components/ReviewSettings"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("does not write before settings have loaded", async () => {
  const writes: string[] = []
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "PUT") {
      writes.push(String(input))
      return new Response(JSON.stringify({}))
    }
    return new Promise(() => {})
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  render(
    <QueryClientProvider client={client}>
      <ReviewSettings canEdit />
    </QueryClientProvider>
  )

  fireEvent.click(screen.getByRole("switch", { name: "PR Summaries" }))
  expect(writes).toEqual([])
  client.clear()
})
