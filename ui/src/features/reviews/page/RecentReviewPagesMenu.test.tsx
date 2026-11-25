/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
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

it.each(["hover", "focus"])(
  "reveals the full PR title on %s without opening recent reviews",
  async (interaction) => {
    vi.stubGlobal("PointerEvent", MouseEvent)
    const title =
      "fix(robin): reveal the complete pull request title when the header is truncated"
    render(
      <QueryClientProvider client={new QueryClient()}>
        <TooltipProvider>
          <RecentReviewPagesMenu
            pr={{ owner: "langchain-ai", repo: "open-swe", number: 123 }}
          >
            {title}
          </RecentReviewPagesMenu>
        </TooltipProvider>
      </QueryClientProvider>
    )
    const trigger = screen.getByRole("link", {
      name: new RegExp(title.replace(/[()]/g, "\\$&")),
    })
    if (interaction === "hover") fireEvent.pointerMove(trigger)
    else {
      fireEvent.keyDown(document, { key: "Tab" })
      fireEvent.focus(trigger)
    }
    expect((await screen.findByRole("tooltip")).textContent).toBe(title)
    expect(screen.queryByText("Recently opened")).toBeNull()
    fireEvent.pointerDown(trigger, { button: 0 })
    expect(await screen.findByText("Recently opened")).not.toBeNull()
  }
)

it.each(["metaKey", "ctrlKey"])(
  "preserves GitHub navigation with %s without opening recent reviews",
  (modifier) => {
    vi.stubGlobal("PointerEvent", MouseEvent)
    render(
      <QueryClientProvider client={new QueryClient()}>
        <TooltipProvider>
          <RecentReviewPagesMenu
            pr={{ owner: "langchain-ai", repo: "open-swe", number: 123 }}
          >
            Fix the review title
          </RecentReviewPagesMenu>
        </TooltipProvider>
      </QueryClientProvider>
    )
    const trigger = screen.getByRole("link", { name: /Fix the review title/ })
    fireEvent.pointerDown(trigger, { button: 0, [modifier]: true })
    expect(trigger.getAttribute("aria-expanded")).toBe("false")
    expect(fireEvent.click(trigger, { [modifier]: true })).toBe(true)
  }
)
