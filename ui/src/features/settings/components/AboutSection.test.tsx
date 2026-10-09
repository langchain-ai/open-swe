/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { renderToString } from "react-dom/server"
import { afterEach, expect, it, vi } from "vitest"

import type { SessionUser } from "@/lib/api"
import { AboutSection } from "./AboutSection"

const user: SessionUser = {
  login: "reader",
  email: null,
  avatar_url: null,
  is_admin: false,
  api_base_url:
    "https://user:secret@backend.example.com/mount?token=secret#private",
  build_info: {
    backend: {
      environment: "preview",
      revision_id: "rev-42",
      commit: "abc123",
      built_at: null,
      package_version: "0.1.0",
    },
    web: { commit: "def456", built_at: null, served: true },
  },
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  delete window.__OPEN_SWE_BUNDLE__
})

it("shows session build and safe API diagnostics without an analytics report", () => {
  window.__OPEN_SWE_BUNDLE__ = {
    commit: "fedcba",
    built_at: "2026-09-11T09:00:00Z",
  }
  render(<AboutSection user={user} />)
  expect(screen.getByText("About")).toBeTruthy()
  expect(screen.getByText(/OPENSWE_ENV:/).textContent).toBe(
    "OPENSWE_ENV: preview"
  )
  expect(screen.getByText("rev-42")).toBeTruthy()
  for (const commit of ["abc123", "def456", "fedcba"]) {
    const link = screen.getByRole("link", { name: commit })
    expect(link.getAttribute("href")).toBe(
      `https://github.com/langchain-ai/open-swe/commit/${commit}`
    )
    expect(link.getAttribute("target")).toBe("_blank")
    expect(link.getAttribute("rel")).toBe("noopener noreferrer")
  }
  expect(screen.getAllByRole("link")).toHaveLength(3)
  expect(screen.getByText(/different from the bundle/)).toBeTruthy()
  expect(screen.getByText(/API:/).textContent).toBe(
    "API: https://backend.example.com /api"
  )
  expect(document.body.textContent).not.toContain("secret")
})

it("retains the running bundle identity when an older backend omits build info", () => {
  window.__OPEN_SWE_BUNDLE__ = {
    commit: "fedcba",
    built_at: "2026-09-11T09:00:00Z",
  }
  render(<AboutSection user={{ ...user, build_info: undefined }} />)
  expect(screen.getByText(/does not report them/)).toBeTruthy()
  expect(screen.getByText(/OPENSWE_ENV:/).textContent).toBe(
    "OPENSWE_ENV: Unavailable"
  )
  expect(screen.getByText("fedcba")).toBeTruthy()
  expect(screen.queryByText(/different from the bundle/)).toBeNull()
})

it("hydrates server markup before showing the browser bundle identity", () => {
  const container = document.createElement("div")
  container.innerHTML = renderToString(<AboutSection user={user} />)
  document.body.appendChild(container)
  expect(container.textContent).not.toContain("This browser is running")

  window.__OPEN_SWE_BUNDLE__ = {
    commit: "fedcba",
    built_at: "2026-09-11T09:00:00Z",
  }
  const onRecoverableError = vi.fn()
  render(<AboutSection user={user} />, {
    container,
    hydrate: true,
    onRecoverableError,
  })

  expect(screen.getByText("fedcba")).toBeTruthy()
  expect(onRecoverableError).not.toHaveBeenCalled()
})

it("does not claim unknown commits differ from the served bundle", () => {
  window.__OPEN_SWE_BUNDLE__ = {
    commit: null,
    built_at: "2026-09-11T09:00:00Z",
  }
  render(<AboutSection user={user} />)
  expect(screen.queryByText(/different from the bundle/)).toBeNull()
  expect(screen.getByText(/comparison .* unavailable/)).toBeTruthy()
})

it("copies only environment diagnostics from session data", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  })
  window.__OPEN_SWE_BUNDLE__ = {
    commit: "running123",
    built_at: "2026-09-11T09:00:00Z",
  }
  const session = {
    ...user,
    token: "private-token",
    build_info: { ...user.build_info!, secret: "private-build-field" },
  }
  render(<AboutSection user={session} />)
  fireEvent.click(screen.getByRole("button", { name: "Copy diagnostics" }))
  expect(
    await screen.findByText("Diagnostics copied to clipboard.")
  ).toBeTruthy()
  const text = writeText.mock.calls[0]![0] as string
  expect(JSON.parse(text)).toMatchObject({
    report: "open-swe-environment-diagnostics",
    api: { origin: "https://backend.example.com", path: "/api" },
    build: { backend: { environment: "preview", revision_id: "rev-42" } },
    running_bundle: { commit: "running123" },
  })
  for (const excluded of [
    "private",
    "secret",
    "reader",
    "pr_report",
    "event_processing",
  ]) {
    expect(text).not.toContain(excluded)
  }
})
