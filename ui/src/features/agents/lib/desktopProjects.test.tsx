/** @vitest-environment jsdom */

import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { useDesktopProjects } from "./desktopProjects"

vi.mock("@/lib/errorReporting", () => ({ reportError: vi.fn() }))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("finishes loading if the desktop project list fails", async () => {
  vi.stubGlobal("openSweDesktop", {
    onProjectsChanged: () => () => {},
    listProjects: () => Promise.reject(new Error("Desktop unavailable")),
  })
  function Projects() {
    const { loaded } = useDesktopProjects()
    return <span>{loaded ? "finished" : "loading"}</span>
  }
  render(<Projects />)
  await waitFor(() => expect(screen.getByText("finished")).toBeTruthy())
})
