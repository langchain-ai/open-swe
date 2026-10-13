/** @vitest-environment jsdom */
import { render, screen } from "@testing-library/react"
import { createElement } from "react"
import { expect, it, vi } from "vitest"
import { ReviewBookmarklet, reviewBookmarkletUrl } from "./ReviewBookmarklet"

it("provides a draggable bookmarklet link", () => {
  render(createElement(ReviewBookmarklet))
  const link = screen.getByRole("link", { name: "👀 Open in Open SWE" })
  expect(link.getAttribute("href")).toBe(
    reviewBookmarkletUrl(window.location.origin)
  )
  expect(link.getAttribute("draggable")).toBe("true")
})

function run(href: string) {
  const open = vi.fn()
  const alert = vi.fn()
  const script = reviewBookmarkletUrl("https://swe.example").slice(
    "javascript:".length
  )
  new Function("location", "window", "alert", script)({ href }, { open }, alert)
  return { open, alert }
}

it("opens the review for the pull request the bookmark is clicked on", () => {
  const { open } = run(
    "https://github.com/langchain-ai/open-swe/pull/3187/files?w=1#diff-abc"
  )
  expect(open).toHaveBeenCalledWith(
    "https://swe.example/agents/reviews/langchain-ai/open-swe/3187",
    "_blank"
  )
})

it("explains itself off a pull request page", () => {
  const { open, alert } = run("https://github.com/langchain-ai/open-swe")
  expect(open).not.toHaveBeenCalled()
  expect(alert).toHaveBeenCalled()
})
