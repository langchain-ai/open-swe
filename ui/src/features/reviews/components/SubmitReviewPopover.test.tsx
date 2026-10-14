/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { useState } from "react"
import { afterEach, expect, it, vi } from "vitest"

import { api, ApiError, type PullRequestReviewEvent } from "@/lib/api"
import { SubmitReviewPopover } from "./SubmitReviewPopover"

vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: "alice", avatar_url: null } }),
}))
vi.mock("@/features/reviews/lib/usePendingReview", () => ({
  usePendingReview: () => ({ comments: [], review: null, invalidate: vi.fn() }),
}))
vi.mock("@/features/reviews/lib/usePullRequestStatus", () => ({
  usePullRequestStatus: () => ({ data: null }),
}))
vi.mock("@/features/reviews/lib/useIsPullRequestAuthor", () => ({
  useIsPullRequestAuthor: () => false,
}))
vi.mock("@/features/reviews/page/queries", () => ({
  useRefreshPullRequest: () => ({ reviewed: vi.fn() }),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it.each([new ApiError(404, "Not Found"), new Error("Failed to fetch")])(
  "preserves the review draft and offers verification without automatically retrying %s",
  async (error) => {
    const request = vi
      .spyOn(api, "submitPullRequestReview")
      .mockRejectedValue(error)
    const client = new QueryClient({
      defaultOptions: { mutations: { retry: 2, retryDelay: 0 } },
    })
    function Form() {
      const [open, setOpen] = useState(true)
      const [verdict, setVerdict] = useState<PullRequestReviewEvent>("APPROVE")
      return (
        <>
          <button onClick={() => setVerdict("COMMENT")}>Change shortcut</button>
          <SubmitReviewPopover
            owner="fakeorg"
            repo="demo"
            number={7}
            open={open}
            onOpenChange={setOpen}
            defaultVerdict={verdict}
          />
        </>
      )
    }
    render(
      <QueryClientProvider client={client}>
        <Form />
      </QueryClientProvider>
    )
    const summary = screen.getByRole("textbox", { name: "Review summary" })
    fireEvent.change(summary, { target: { value: "Looks good to me" } })
    fireEvent.click(screen.getByRole("button", { name: "Submit review" }))
    await screen.findByRole("alert")
    fireEvent.click(screen.getByRole("button", { name: "Change shortcut" }))
    expect((summary as HTMLTextAreaElement).value).toBe("Looks good to me")
    expect(
      screen
        .getByRole("radio", { name: /Approve/ })
        .getAttribute("aria-checked")
    ).toBe("true")
    expect(
      screen
        .getByRole("link", { name: "Check review on GitHub" })
        .getAttribute("href")
    ).toBe("https://github.com/fakeorg/demo/pull/7#pullrequestreview")
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1))
    expect(request).toHaveBeenCalledWith("fakeorg", "demo", 7, {
      event: "APPROVE",
      body: "Looks good to me",
    })
  }
)
