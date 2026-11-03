// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { GitHubNavigationConfirmation } from "./GitHubNavigationConfirmation"
import { externalNavigation } from "@/lib/externalNavigation"

const url = "https://github.com/langchain-ai/open-swe/pull/42"

afterEach(() => {
  cleanup()
  externalNavigation.dismiss()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it("blocks a GitHub link until confirmed and lets Cancel keep the user here", async () => {
  const open = vi.spyOn(window, "open").mockReturnValue(null)
  render(
    <>
      <GitHubNavigationConfirmation />
      <a href={url} target="_blank">
        GitHub
      </a>
    </>
  )
  const link = screen.getByRole("link", { name: "GitHub" })
  link.focus()
  fireEvent.click(link)
  expect(screen.getByRole("dialog", { name: "Are you sure?" })).toBeTruthy()
  expect(open).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  expect(open).not.toHaveBeenCalled()
  expect(document.activeElement).toBe(link)
  fireEvent.click(link)
  fireEvent.click(screen.getByRole("button", { name: "Open GitHub" }))
  expect(open).toHaveBeenCalledExactlyOnceWith(
    url,
    "_blank",
    "noopener,noreferrer"
  )
})

it("confirms middle-clicks and programmatic GitHub actions, but not other hosts or desktop", () => {
  const open = vi.spyOn(window, "open").mockReturnValue(null)
  render(
    <>
      <GitHubNavigationConfirmation />
      <a href={url}>GitHub</a>
    </>
  )
  fireEvent(
    screen.getByRole("link"),
    new MouseEvent("auxclick", { button: 1, bubbles: true, cancelable: true })
  )
  expect(externalNavigation.pending.getState()?.target).toBe("_blank")
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })
  expect(externalNavigation.pending.getState()).toBeNull()
  externalNavigation.open("https://github.com.evil.example/path")
  expect(open).toHaveBeenLastCalledWith(
    "https://github.com.evil.example/path",
    "_blank",
    "noopener,noreferrer"
  )
  fireEvent.click(screen.getByRole("link"), { ctrlKey: true })
  expect(externalNavigation.pending.getState()?.target).toBe("_blank")
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  vi.stubGlobal("openSweDesktop", {})
  externalNavigation.open(url)
  expect(externalNavigation.pending.getState()).toBeNull()
  expect(open).toHaveBeenLastCalledWith(url, "_blank", "noopener,noreferrer")
})

it("shows a confirmation for an Open on GitHub action", () => {
  const open = vi.spyOn(window, "open").mockReturnValue(null)
  render(
    <>
      <GitHubNavigationConfirmation />
      <button onClick={() => externalNavigation.open(url)}>
        Open on GitHub
      </button>
    </>
  )
  fireEvent.click(screen.getByRole("button", { name: "Open on GitHub" }))
  expect(screen.getByRole("dialog", { name: "Are you sure?" })).toBeTruthy()
  expect(open).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Open GitHub" }))
  expect(open).toHaveBeenCalledExactlyOnceWith(
    url,
    "_blank",
    "noopener,noreferrer"
  )
})
