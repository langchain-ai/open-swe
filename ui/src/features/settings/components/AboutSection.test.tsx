/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { renderToString } from "react-dom/server"
import { afterEach, expect, it, vi } from "vitest"

import type { SessionUser } from "@/lib/api"
import { AboutSection } from "./AboutSection"

const user: SessionUser = {
  login: "reader",
  email: null,
  avatar_url: null,
  is_admin: false,
  default_workspace: null,
  api_base_url:
    "https://user:secret@backend.example.com/mount?token=secret#private",
  build_info: {
    backend: {
      revision_id: "rev-42",
      commit: "abc123",
      built_at: null,
      package_version: "0.1.0",
    },
    dashboard: { commit: "def456", built_at: null, served: true },
  },
}

afterEach(() => {
  cleanup()
  delete window.__OPEN_SWE_BUNDLE__
})

it("shows session build and safe API diagnostics without an analytics report", () => {
  window.__OPEN_SWE_BUNDLE__ = {
    commit: "fedcba",
    built_at: "2026-09-11T09:00:00Z",
  }
  render(<AboutSection user={user} />)
  expect(screen.getByText("About")).toBeTruthy()
  expect(screen.getByText("rev-42")).toBeTruthy()
  expect(screen.getByText("abc123")).toBeTruthy()
  expect(screen.getByText("def456")).toBeTruthy()
  expect(screen.getByText("fedcba")).toBeTruthy()
  expect(screen.getByText(/different from the bundle/)).toBeTruthy()
  expect(screen.getByText(/API:/).textContent).toBe(
    "API: https://backend.example.com /dashboard/api"
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
