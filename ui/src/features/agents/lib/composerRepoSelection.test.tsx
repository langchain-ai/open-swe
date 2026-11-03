/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { useSyncExternalStore } from "react"
import { afterEach, beforeEach, expect, it } from "vitest"

import { composerRepoSelection } from "./composerRepoSelection"

beforeEach(() => window.localStorage.clear())
afterEach(cleanup)

function RepoSelection({ userId }: { userId: string }) {
  const repo = useSyncExternalStore(
    composerRepoSelection.subscribe,
    () => composerRepoSelection.read(userId),
    () => undefined
  )
  return (
    <button onClick={() => composerRepoSelection.write(userId, "acme/docs")}>
      {repo === undefined ? "Default repository" : (repo ?? "No repository")}
    </button>
  )
}

it("restores an explicit selection after remount without sharing it across accounts", () => {
  const first = render(<RepoSelection userId="alice-id" />)
  fireEvent.click(screen.getByRole("button", { name: "Default repository" }))
  first.unmount()
  const second = render(<RepoSelection userId="alice-id" />)
  expect(screen.getByRole("button").textContent).toBe("acme/docs")
  second.rerender(<RepoSelection userId="bob-id" />)
  expect(screen.getByRole("button").textContent).toBe("Default repository")
})

it("distinguishes choosing no repository from not having chosen yet", () => {
  composerRepoSelection.write("alice-id", null)
  render(<RepoSelection userId="alice-id" />)
  expect(screen.getByRole("button").textContent).toBe("No repository")
  expect(composerRepoSelection.read("bob-id")).toBeUndefined()
})

it("ignores stored values that are not a repository selection", () => {
  window.localStorage.setItem(composerRepoSelection.key("alice-id"), "42")
  expect(composerRepoSelection.read("alice-id")).toBeUndefined()
  expect(composerRepoSelection.read("")).toBeUndefined()
})
