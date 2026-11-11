/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { githubUrls } from "./githubUrls"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it.each(["metaKey", "ctrlKey"])(
  "%s opens internal PR links on GitHub before their click handlers run",
  (modifier) => {
    const open = vi.spyOn(window, "open").mockReturnValue(null)
    const navigate = vi.fn()
    render(
      <div onClickCapture={githubUrls.openModifiedLink}>
        <a
          href="/agents/reviews/acme/app/42?tab=files#discussion"
          onClick={navigate}
        >
          <span>Review PR</span>
        </a>
        <a href="/acme/app/pull/43">Shared PR</a>
      </div>
    )
    fireEvent.click(screen.getByText("Review PR"), { [modifier]: true })
    expect(open).toHaveBeenCalledWith(
      "https://github.com/acme/app/pull/42",
      "_blank",
      "noopener,noreferrer"
    )
    expect(navigate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText("Shared PR"), { [modifier]: true })
    expect(open).toHaveBeenLastCalledWith(
      "https://github.com/acme/app/pull/43",
      "_blank",
      "noopener,noreferrer"
    )
  }
)

it("preserves ordinary clicks, non-PR links, and external links", () => {
  const open = vi.spyOn(window, "open").mockReturnValue(null)
  const click = vi.fn((event) => event.preventDefault())
  render(
    <div onClickCapture={githubUrls.openModifiedLink} onClick={click}>
      <a href="/agents/reviews/acme/app/42">Review PR</a>
      <a href="/agents/reviews">Review list</a>
      <a href="https://example.com/agents/reviews/acme/app/42">External</a>
      <a href="https://github.com/acme/app/pull/42">GitHub</a>
    </div>
  )
  fireEvent.click(screen.getByText("Review PR"))
  for (const name of ["Review list", "External", "GitHub"])
    fireEvent.click(screen.getByText(name), { metaKey: true })
  expect(click).toHaveBeenCalledTimes(4)
  expect(open).not.toHaveBeenCalled()
})
