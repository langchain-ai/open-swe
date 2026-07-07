/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

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

describe("auth redirect helpers", () => {
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

  it("resolves login targets to absolute same-origin URLs", () => {
    const path = rememberAuthRedirect("/agents/thread-1/plan?from=slack#review")

    const target = `${window.location.origin}/agents/thread-1/plan?from=slack#review`

    expect(authRedirectUrl(path)).toBe(target)
    expect(loginUrl(authRedirectUrl(path))).toContain(
      encodeURIComponent(target)
    )
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
    expect(loginUrl(authRedirectUrl(currentAuthRedirectPath()))).toContain(
      encodeURIComponent(
        `${window.location.origin}/agents/thread-1/plan?from=slack`
      )
    )
  })
})
