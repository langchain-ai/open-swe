/** @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { loginUrl } from "./api"
import {
  AUTH_REDIRECT_STORAGE_KEY,
  DEFAULT_AUTH_REDIRECT,
  authRedirectPathFromLocation,
  authRedirectUrl,
  consumeAuthRedirect,
  currentAuthRedirectPath,
  getRememberedAuthRedirect,
  rememberAuthRedirect,
  sanitizeAuthRedirect,
} from "./auth-redirect-core"

beforeEach(() => {
  window.sessionStorage.clear()
  window.history.pushState({}, "", "/")
})

afterEach(() => {
  Reflect.deleteProperty(window, "openSweDesktop")
})

describe("auth redirect helpers", () => {
  it("only selects LangSmith login for the Agent Workspace desktop distribution", () => {
    window.history.pushState({}, "", "/login?agent_workspace=true")
    expect(loginUrl("/agents")).toContain("/auth/login?")
    Object.defineProperty(window, "openSweDesktop", {
      configurable: true,
      value: { isDesktop: true, appVariant: "agent-workspace" },
    })
    expect(loginUrl("/agents")).toContain("/auth/langsmith/login?")
    expect(loginUrl("/agents")).toContain("redirect_to=%2Fagents")
    Object.defineProperty(window, "openSweDesktop", {
      configurable: true,
      value: { isDesktop: true, appVariant: "open-swe" },
    })
    expect(loginUrl("/agents")).toContain("/auth/login?")
  })

  it("captures protected route targets as relative paths", () => {
    const path = authRedirectPathFromLocation({
      pathname: "/agents/thread-1/plan",
      search: "?from=slack",
      hash: "#review",
    })

    expect(path).toBe("/agents/thread-1/plan?from=slack#review")
    expect(rememberAuthRedirect(path)).toBe(path)
    expect(window.sessionStorage.getItem(AUTH_REDIRECT_STORAGE_KEY)).toBe(path)
  })

  it("builds login targets from relative same-origin paths", () => {
    const path = rememberAuthRedirect("/agents/thread-1/plan?from=slack#review")

    expect(authRedirectUrl(path)).toBe(
      `${window.location.origin}/agents/thread-1/plan?from=slack#review`
    )
    expect(loginUrl(path)).toContain(encodeURIComponent(path))
  })

  it("consumes remembered targets and clears session storage", () => {
    rememberAuthRedirect("/agents/thread-1/plan")

    expect(consumeAuthRedirect()).toBe("/agents/thread-1/plan")
    expect(getRememberedAuthRedirect()).toBeNull()
  })

  it("falls back for unsafe targets", () => {
    expect(
      sanitizeAuthRedirect("https://evil.example/agents/thread-1/plan")
    ).toBe(DEFAULT_AUTH_REDIRECT)
    expect(sanitizeAuthRedirect("//evil.example/agents/thread-1/plan")).toBe(
      DEFAULT_AUTH_REDIRECT
    )
    expect(sanitizeAuthRedirect("/login?redirect=/agents/thread-1/plan")).toBe(
      DEFAULT_AUTH_REDIRECT
    )
  })

  it("builds a plan sign-in target for the current plan URL", () => {
    window.history.pushState({}, "", "/agents/thread-1/plan?from=slack")

    expect(currentAuthRedirectPath()).toBe("/agents/thread-1/plan?from=slack")
    expect(loginUrl(currentAuthRedirectPath())).toContain(
      encodeURIComponent("/agents/thread-1/plan?from=slack")
    )
    expect(loginUrl()).toContain(
      encodeURIComponent("/agents/thread-1/plan?from=slack")
    )
  })
})
