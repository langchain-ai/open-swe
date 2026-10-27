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

function run(href: string, links: string[] = []) {
  const open = vi.fn()
  const alert = vi.fn()
  const script = reviewBookmarkletUrl("https://swe.example").slice(
    "javascript:".length
  )
  const page = document.createElement("div")
  for (const linkedUrl of links) {
    const link = document.createElement("a")
    link.href = linkedUrl
      .replace("https://github.com/", "https://swe.example/agents/reviews/")
      .replace("/pull/", "/")
    link.setAttribute("data-thread-pull-request", linkedUrl)
    page.append(link)
  }
  new Function("location", "window", "alert", "document", script)(
    { href },
    { open },
    alert,
    page
  )
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

it("opens the latest linked PR review from an agent thread", () => {
  const { open } = run(
    "https://swe.example/agents/90e5819d-38fd-5794-b603-4b918c64de97?subagent=task-1",
    [
      "https://github.com/langchain-ai/open-swe/pull/3187",
      "https://github.com/langchain-ai/open-swe/pull/4024",
    ]
  )
  expect(open).toHaveBeenCalledWith(
    "https://swe.example/agents/reviews/langchain-ai/open-swe/4024",
    "_blank"
  )
})

it("explains when a thread has no loaded linked PR", () => {
  const { open, alert } = run(
    "https://swe.example/agents/90e5819d-38fd-5794-b603-4b918c64de97"
  )
  expect(open).not.toHaveBeenCalled()
  expect(alert).toHaveBeenCalledWith(
    expect.stringContaining("no loaded linked")
  )
})

it.each([
  "https://example.com/github.com/langchain-ai/open-swe/pull/4024",
  "https://github.com.evil.example/langchain-ai/open-swe/pull/4024",
])("does not treat an unrelated URL as a GitHub PR: %s", (url) => {
  const { open, alert } = run(url)
  expect(open).not.toHaveBeenCalled()
  expect(alert).toHaveBeenCalled()
})

it("explains itself off a pull request page", () => {
  const { open, alert } = run("https://github.com/langchain-ai/open-swe")
  expect(open).not.toHaveBeenCalled()
  expect(alert).toHaveBeenCalled()
})
