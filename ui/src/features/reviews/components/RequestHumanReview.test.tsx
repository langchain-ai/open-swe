/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import type { OpenPullRequest } from "@/lib/api"
import { RequestHumanReview } from "./RequestHumanReview"

vi.mock("@/lib/api", () => ({ api: { requestHumanReview: vi.fn() } }))

const pr = {
  repo: "acme/app",
  number: 1,
  title: "Fix",
  draft: false,
  reviewChannel: "",
} as OpenPullRequest

afterEach(cleanup)

function mount(reviewChannel: string) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RequestHumanReview pr={{ ...pr, reviewChannel }} />
    </QueryClientProvider>
  )
}

it("only offers human review when the repository configures a review channel", () => {
  mount("")
  expect(
    screen.queryByRole("button", { name: "Request review in Slack" })
  ).toBeNull()
  cleanup()
  mount("#eng-reviews")
  expect(
    screen.getByRole<HTMLButtonElement>("button", {
      name: "Request review in Slack",
    }).disabled
  ).toBe(false)
})
