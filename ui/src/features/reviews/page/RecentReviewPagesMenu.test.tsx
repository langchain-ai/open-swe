/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { RecentReviewPagesMenu } from "./RecentReviewPagesMenu"

vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: "alice" } }),
}))
vi.mock("./queries", () => ({
  reviewQueries: {
    detail: () => ({ queryKey: ["detail"], queryFn: async () => null }),
  },
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it.each(["metaKey", "ctrlKey"])(
  "preserves GitHub navigation with %s without opening recent reviews",
  (modifier) => {
    vi.stubGlobal("PointerEvent", MouseEvent)
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RecentReviewPagesMenu
          pr={{ owner: "langchain-ai", repo: "open-swe", number: 123 }}
        >
          Fix the review title
        </RecentReviewPagesMenu>
      </QueryClientProvider>
    )
    const trigger = screen.getByRole("link", { name: /Fix the review title/ })
    fireEvent.pointerDown(trigger, { button: 0, [modifier]: true })
    expect(trigger.getAttribute("aria-expanded")).toBe("false")
    expect(fireEvent.click(trigger, { [modifier]: true })).toBe(true)
  }
)
