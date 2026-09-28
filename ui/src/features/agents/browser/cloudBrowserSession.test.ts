import { describe, expect, it } from "vitest"

import {
  applyNavigationUpdate,
  isVisiblePageTarget,
  navFromTarget,
} from "@/features/agents/browser/cloudBrowserSession"

describe("cloud browser target filtering", () => {
  it("shows page targets and hides Chromium's own surfaces", () => {
    const base = { targetId: "t", title: "", attached: false }
    expect(
      isVisiblePageTarget({
        ...base,
        type: "page",
        url: "http://localhost:5173/",
      })
    ).toBe(true)
    expect(
      isVisiblePageTarget({ ...base, type: "page", url: "about:blank" })
    ).toBe(true)
    expect(
      isVisiblePageTarget({
        ...base,
        type: "page",
        url: "devtools://devtools/x",
      })
    ).toBe(false)
    expect(
      isVisiblePageTarget({ ...base, type: "service_worker", url: "http://x/" })
    ).toBe(false)
  })
})

describe("navigation state merging", () => {
  it("treats about:blank as an idle tab and keeps loading while a title arrives", () => {
    expect(
      navFromTarget(
        { kind: "idle" },
        { url: "about:blank", title: "about:blank" }
      )
    ).toEqual({ kind: "idle" })
    expect(
      navFromTarget(
        { kind: "loading", url: "http://a/", title: "" },
        { url: "http://a/", title: "A" }
      )
    ).toEqual({ kind: "loading", url: "http://a/", title: "A" })
    expect(
      navFromTarget({ kind: "idle" }, { url: "http://a/", title: "A" })
    ).toEqual({
      kind: "success",
      url: "http://a/",
      title: "A",
    })
  })

  it("applies page-session updates in order", () => {
    const loading = applyNavigationUpdate(
      { kind: "idle" },
      { url: "http://a/", loading: true }
    )
    expect(loading).toEqual({ kind: "loading", url: "http://a/", title: "" })
    const failed = applyNavigationUpdate(loading, {
      url: "http://a/",
      loading: false,
      failed: "net::ERR_CONNECTION_REFUSED",
    })
    expect(failed).toMatchObject({
      kind: "failed",
      description: "net::ERR_CONNECTION_REFUSED",
    })
    // Chromium's did-stop-loading after a failure must not turn it into a success.
    expect(applyNavigationUpdate(failed, { loading: false })).toBe(failed)
    // A fresh committed navigation clears the failure.
    expect(
      applyNavigationUpdate(failed, { url: "http://b/", failed: null })
    ).toEqual({
      kind: "success",
      url: "http://b/",
      title: "",
    })
    expect(applyNavigationUpdate(loading, { loading: false })).toEqual({
      kind: "success",
      url: "http://a/",
      title: "",
    })
  })
})
