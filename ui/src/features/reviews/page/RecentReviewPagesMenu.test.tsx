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

function title() {
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
  return screen.getByRole("link", { name: /Fix the review title/ })
}

it("opens recent reviews on a normal click without navigating", () => {
  const trigger = title()
  fireEvent.pointerDown(trigger, { button: 0 })
  expect(trigger.getAttribute("aria-expanded")).toBe("true")
  expect(fireEvent.click(trigger)).toBe(false)
  expect(screen.getByRole("menu")).toBeDefined()
})

it.each(["metaKey", "ctrlKey"])(
  "preserves GitHub navigation with %s without opening recent reviews",
  (modifier) => {
    const trigger = title()
    expect(trigger.getAttribute("href")).toBe(
      "https://github.com/langchain-ai/open-swe/pull/123"
    )
    expect(trigger.getAttribute("target")).toBe("_blank")
    fireEvent.pointerDown(trigger, { button: 0, [modifier]: true })
    expect(trigger.getAttribute("aria-expanded")).toBe("false")
    expect(fireEvent.click(trigger, { [modifier]: true })).toBe(true)
  }
)

it("still opens recent reviews from the keyboard", () => {
  const trigger = title()
  fireEvent.keyDown(trigger, { key: "Enter" })
  expect(trigger.getAttribute("aria-expanded")).toBe("true")
  expect(screen.getByRole("menu")).toBeDefined()
})
